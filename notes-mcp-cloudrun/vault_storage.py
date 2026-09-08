"""Objecten uit de vault-bucket oplijsten en ophalen.

De client wordt geinjecteerd, zodat alles zonder Supabase te testen is --
hetzelfde patroon als compute_screener(universe, fetch).

Eén regel is niet onderhandelbaar: een transportfout mag nooit op data
lijken. Een lege lijst teruggeven terwijl de bucket onbereikbaar is, is een
uitspraak over de vault die niet waar is, en die leugen blijft hangen zolang
de aanroeper hem gelooft.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor


class StorageUnavailable(RuntimeError):
    """De bucket kon niet bevraagd worden. Niet te verwarren met leeg."""


class NoteNotFound(LookupError):
    """De sleutel bestaat niet. Niet te verwarren met een leeg bestand."""


def _error_code(exc) -> str:
    """De S3-foutcode, of "" als de exceptie er geen draagt."""
    response = getattr(exc, "response", None) or {}
    if not isinstance(response, dict):
        return ""
    return (response.get("Error") or {}).get("Code", "") or ""


def _http_status(exc):
    """De HTTP-status van de mislukte aanroep, of None."""
    response = getattr(exc, "response", None) or {}
    if not isinstance(response, dict):
        return None
    return (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")


def _is_missing(exc) -> bool:
    """Zeker weten dat alléén deze sleutel ontbreekt, zonder verder te kijken.

    Alleen een expliciete NoSuchKey haalt dit. Alles wat grover is -- een kale
    404 bijvoorbeeld -- moet eerst nog bewijzen dat de bucket zelf leeft; zie
    _may_be_missing en VaultStore.bucket_reachable.
    """
    return _error_code(exc) == "NoSuchKey"


def _may_be_missing(exc) -> bool:
    """Kán dit een ontbrekende sleutel zijn, of is het een storing?

    Supabase Storage geeft bij een ontbrekend object HTTP 404 met een LEGE
    Error.Code -- gemeten tegen de live endpoint op 2026-09-08. Geen NoSuchKey.
    Daardoor viel elk verkeerd pad door naar StorageUnavailable, en meldde de
    server "de opslag is onbereikbaar" terwijl er alleen een typefout in het
    pad zat. Dat is de omgekeerde leugen van waar deze module voor gebouwd is:
    niet een storing die als data leest, maar data die als storing leest. Even
    schadelijk, want de aanroeper stopt met zoeken en gaat elders raden.

    Een verdwenen bucket geeft exact dezelfde 404, dus dit alleen is geen
    bewijs. Vandaar dat de aanroeper er een bucket-probe achteraan doet.
    """
    return _http_status(exc) == 404


class RevisionConflict(RuntimeError):
    """De notitie is sinds de gelezen revisie gewijzigd, of bestaat al."""


class VaultStore:
    def __init__(self, client, bucket: str):
        self._client = client
        self._bucket = bucket

    def bucket_reachable(self) -> bool:
        """Leeft de bucket? Eén lijst-call van één sleutel.

        Het enige signaal dat "deze notitie bestaat niet" onderscheidt van
        "de opslag ligt plat", omdat Supabase op allebei een lege 404 geeft.
        Alleen op het misser-pad, dus het normale lezen betaalt er niets voor.
        """
        try:
            self._client.list_objects_v2(Bucket=self._bucket, MaxKeys=1)
        except Exception:
            return False
        return True

    def list_keys(self, prefix: str) -> list[str]:
        """Alle objectsleutels onder prefix, over alle pagina's heen."""
        keys: list[str] = []
        try:
            pages = self._client.get_paginator("list_objects_v2").paginate(
                Bucket=self._bucket, Prefix=prefix)
            for page in pages:
                for item in page.get("Contents") or []:
                    keys.append(item["Key"])
        except Exception as e:
            raise StorageUnavailable(f"kon {prefix!r} niet oplijsten: {e}") from e
        return keys

    def get(self, key: str) -> tuple[str, str]:
        """(tekst, revisie) van één object."""
        try:
            obj = self._client.get_object(Bucket=self._bucket, Key=key)
        except Exception as e:
            if _is_missing(e):
                raise NoteNotFound(key) from e
            if _may_be_missing(e) and self.bucket_reachable():
                raise NoteNotFound(key) from e
            raise StorageUnavailable(f"kon {key!r} niet ophalen: {e}") from e
        text = obj["Body"].read().decode("utf-8", "replace")
        return text, (obj.get("ETag") or "").strip('"')

    def get_many(self, keys: list[str], max_workers: int = 16) -> dict[str, str]:
        """Meerdere objecten parallel. Sleutels die intussen weg zijn vallen
        stil af; een transportfout doet dat niet en komt naar boven."""
        if not keys:
            return {}

        def one(key):
            try:
                return key, self.get(key)[0]
            except NoteNotFound:
                return key, None

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            pairs = list(pool.map(one, keys))
        return {k: v for k, v in pairs if v is not None}


    def put(self, key: str, text: str, expected_revision: str | None = None) -> str:
        """Schrijf één notitie weg en geef de nieuwe revisie terug.

        `expected_revision` is de revisie die de schrijver dacht te overschrijven:

        - None + de sleutel bestaat niet  -> aanmaken
        - None + de sleutel bestaat wél   -> RevisionConflict (geen blinde overschrijving)
        - gelijk aan de huidige revisie   -> bijwerken
        - ongelijk                        -> RevisionConflict

        De controle is lezen-dan-schrijven, geen atomair If-Match: Supabase's
        S3-laag ondersteunt conditionele writes niet betrouwbaar. Tussen de
        lezing en de schrijving past dus theoretisch een andere schrijver. Dat
        is hier aanvaardbaar -- één mens, en de synchronisatieplugin schrijft
        naar dezelfde bucket in trage rondes -- maar het is een aanname en geen
        garantie, en die staat hier zodat niemand hem later voor een garantie
        aanziet.
        """
        try:
            current = self.get(key)[1]
        except NoteNotFound:
            current = None

        if current is None and expected_revision:
            raise RevisionConflict(
                f"revisie {expected_revision!r} meegegeven, maar {key!r} bestaat niet")
        if current is not None and expected_revision is None:
            raise RevisionConflict(
                f"{key!r} bestaat al (revisie {current!r}); lees hem eerst en geef "
                f"die revisie mee om hem bewust te overschrijven")
        if current is not None and expected_revision != current:
            raise RevisionConflict(
                f"{key!r} is gewijzigd sinds {expected_revision!r} (nu {current!r}); "
                f"lees opnieuw en voeg je wijziging daarop toe")

        try:
            response = self._client.put_object(
                Bucket=self._bucket, Key=key,
                Body=text.encode("utf-8"), ContentType="text/markdown")
        except Exception as e:
            raise StorageUnavailable(f"kon {key!r} niet schrijven: {e}") from e
        return (response.get("ETag") or "").strip('"')


def make_client():
    """boto3-client tegen Supabase Storage, uit de omgeving."""
    import boto3

    endpoint = os.environ["SUPABASE_S3_ENDPOINT"]
    region = os.environ.get("SUPABASE_S3_REGION", "eu-west-3")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=os.environ["SUPABASE_S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["SUPABASE_S3_SECRET_ACCESS_KEY"],
    )
