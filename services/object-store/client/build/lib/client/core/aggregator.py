from client.api.schemas import CropResult, InferenceResultRecord, ModelResult
from client.core.inference_client import BatchOutcome


def build_record(
    obj_store_path: str, outcomes: list[BatchOutcome]
) -> InferenceResultRecord:
    crop_results: list[CropResult] = []
    completed = 0
    failed = 0
    deployment_type: str | None = None

    for outcome in outcomes:
        if outcome.scores is None:
            for crop_id in outcome.batch.crop_ids:
                crop_results.append(CropResult(cropId=crop_id, status="failed"))
                failed += 1
        else:
            if deployment_type is None and outcome.deployment_type is not None:
                deployment_type = outcome.deployment_type
            for crop_id, s in zip(outcome.batch.crop_ids, outcome.scores):
                crop_results.append(
                    CropResult(
                        cropId=crop_id,
                        status="completed",
                        score=s.score,
                        verdict=s.verdict,
                        threshold=s.threshold,
                        old_model=ModelResult(
                            version=s.old_model.version,
                            score=s.old_model.score,
                            verdict=s.old_model.verdict,
                        ),
                        new_model=ModelResult(
                            version=s.new_model.version,
                            score=s.new_model.score,
                            verdict=s.new_model.verdict,
                        ),
                    )
                )
                completed += 1

    total = completed + failed
    if failed == 0:
        status = "completed"
    elif completed == 0:
        status = "failed"
    else:
        status = "partial"

    return InferenceResultRecord(
        objStorePath=obj_store_path,
        status=status,
        cropCount=total,
        completedCount=completed,
        failedCount=failed,
        deployment_type=deployment_type,
        cropResults=crop_results,
    )
