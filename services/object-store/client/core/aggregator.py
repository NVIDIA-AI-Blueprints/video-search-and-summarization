from client.api.schemas import CropResult, InferenceResultRecord, ModelResult
from client.core.inference_client import BatchOutcome


def build_record(
    obj_store_path: str, outcomes: list[BatchOutcome]
) -> InferenceResultRecord:
    crop_results: list[CropResult] = []
    completed = 0
    failed = 0
    deployment_type: str | None = None
    model_name: str | None = None

    for outcome in outcomes:
        if outcome.scores is None:
            for crop_id in outcome.batch.crop_ids:
                crop_results.append(CropResult(cropId=crop_id, status="failed"))
                failed += 1
        else:
            if deployment_type is None and outcome.deployment_type is not None:
                deployment_type = outcome.deployment_type
            if model_name is None and outcome.model_name is not None:
                model_name = outcome.model_name
            for crop_id, s in zip(outcome.batch.crop_ids, outcome.scores):
                crop_results.append(
                    CropResult(
                        cropId=crop_id,
                        status="completed",
                        score=s.score,
                        verdict=s.verdict,
                        threshold=s.threshold,
                        old_model=ModelResult(
                            model_name=s.old_model.model_name,
                            version=s.old_model.version,
                            score=s.old_model.score,
                            verdict=s.old_model.verdict,
                        ) if s.old_model is not None else None,
                        new_model=ModelResult(
                            model_name=s.new_model.model_name,
                            version=s.new_model.version,
                            score=s.new_model.score,
                            verdict=s.new_model.verdict,
                        ) if s.new_model is not None else None,
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
        model_name=model_name,
        cropResults=crop_results,
    )
