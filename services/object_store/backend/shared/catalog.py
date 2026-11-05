"""Which Elasticsearch catalog a request addresses.

AOI inspections and Smart City frames are indexed separately (``aoi_*`` and
``sc_*``) and share no documents, so every catalog-reading endpoint has to be
told which one it means. The value travels in the URL — ``/v1/aoi/...`` and
``/v1/its/...`` — and is resolved to an index pattern here.

This lives in ``shared`` rather than beside either endpoint: ``kpi_service``
already imports ``SORT_SPEC`` from ``snapshot_service``, so putting the helper
in one of them and importing it from the other would close an import cycle.
"""

from typing import Literal

from backend.config import settings

Catalog = Literal["aoi", "its"]


def catalog_index(catalog: Catalog) -> str:
    """Index pattern holding the records of one catalog."""
    return settings.sc_es_index if catalog == "its" else settings.es_index


# Selection keys a snapshot may filter on, per catalog. Derived from the index
# templates (deploy/docker/config/database/elasticsearch/templates): every
# metadata.content_metadata leaf that a term/terms filter can address, with
# nested objects spelled out as the dotted paths compile_selection emits.
# Excluded because a term filter cannot address them: the `nested` fields
# (sc objects[], aoi cropInfo[]) and the geo_point locations.
# created_after is an API-level key, not a record field. expiredAt is left out:
# it is set by the retention pass (sc_gc), and every ITS selection excludes the
# documents that carry it.
SELECTION_FIELDS: dict[Catalog, frozenset[str]] = {
    "aoi": frozenset(
        {
            "created_after",
            "doc_type",
            "uuid",
            "doc_i",
            "chunkIdx",
            "version",
            "description",
            "timestamp",
            "tag",
            "goldenRef",
            "machineId",
            "location",
            "jobType",
            "defectCount",
            "objStorePath",
            "imageType",
            "imageSize",
            "inferenceSummary.status",
            "inferenceSummary.cropCount",
            "inferenceSummary.completedCount",
            "inferenceSummary.failedCount",
            "inferenceSummary.deployment_type",
            "inferenceSummary.model_name",
            "inferenceSummary.updatedAt",
        }
    ),
    "its": frozenset(
        {
            "created_after",
            "doc_type",
            "uuid",
            "doc_i",
            "chunkIdx",
            "schemaVersion",
            "frameId",
            "timestamp",
            "ingestedAt",
            "sensorId",
            "videoPath",
            "description",
            "objStorePath",
            "tag",
            "imageType",
            "imageSize",
            "imageWidth",
            "imageHeight",
            "objectCount",
            "objectTypes",
            "sensor.id",
            "sensor.type",
            "sensor.description",
            "sensor.altitude",
            "sensor.coordinate.x",
            "sensor.coordinate.y",
            "sensor.coordinate.z",
            "place.id",
            "place.name",
            "place.type",
            "place.altitude",
            "place.coordinate.x",
            "place.coordinate.y",
            "place.coordinate.z",
            "place.info.name",
            "place.info.lane",
            "place.info.level",
            "analyticsModule.id",
            "analyticsModule.description",
            "analyticsModule.source",
            "analyticsModule.version",
        }
    ),
}
