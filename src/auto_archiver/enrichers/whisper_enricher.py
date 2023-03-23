import traceback
import requests, time
from loguru import logger

from . import Enricher
from ..core import Metadata, Media, ArchivingContext
from ..storages import S3Storage


class WhisperEnricher(Enricher):
    """
    Connects with a Whisper API service to get texts out of audio
    whisper API repository: TODO
    Only works if an S3 compatible storage is used
    """
    name = "whisper_enricher"

    def __init__(self, config: dict) -> None:
        # without this STEP.__init__ is not called
        super().__init__(config)
        assert type(self.api_key) == str and len(self.api_key) > 0, "please provide a value for the whisper_enricher api_key"
        self.timeout = int(self.timeout)

    @staticmethod
    def configs() -> dict:
        return {
            "api_endpoint": {"default": "https://whisper.spoettel.dev/api/v1", "help": "WhisperApi api endpoint"},
            "api_key": {"default": None, "help": "WhisperApi api key for authentication"},
            "timeout": {"default": 90, "help": "How many seconds to wait at most for a successful job completion."},
            "action": {"default": "translation", "help": "which Whisper operation to execute", "choices": ["transcript", "translation", "language_detection"]},

        }

    def enrich(self, to_enrich: Metadata) -> None:
        if not self._get_s3_storage():
            logger.error("WhisperEnricher: To use the WhisperEnricher you need to use S3Storage so files are accessible publicly to the whisper service being called.")
            return

        url = to_enrich.get_url()
        logger.debug(f"WHISPER[{self.action}]: iterating media items for {url=}.")

        job_results = {}
        for i, m in enumerate(to_enrich.media):
            if m.is_video() or m.is_audio():
                m.store()
                try:
                    job_id = self.submit_job(m)
                    job_results[job_id] = False
                    logger.debug(f"JOB SUBMITTED: {job_id=} for {m.key=}")
                    to_enrich.media[i].set("whisper_model", {"job_id": job_id})
                except Exception as e:
                    logger.error(f"Failed to submit whisper job for {m.filename=} with error {e}\n{traceback.format_exc()}")

        job_results = self.check_jobs(job_results)

        for i, m in enumerate(to_enrich.media):
            if m.is_video() or m.is_audio():
                job_id = to_enrich.media[i].get("whisper_model")["job_id"]
                to_enrich.media[i].set("whisper_model", {
                    "job_id": job_id,
                    self.action: job_results[job_id]
                })

    def submit_job(self, media: Media):
        s3 = self._get_s3_storage()
        s3_url = s3.get_cdn_url(media)
        assert s3_url in media.urls, f"Could not find S3 url ({s3_url}) in list of stored media urls "
        payload = {
            "url": s3_url,
            "type": self.action,
            # "language": "string" # may be a config
        }
        response = requests.post(f'{self.api_endpoint}/jobs', json=payload, headers={'Authorization': f'Bearer {self.api_key}'})
        assert response.status_code == 201, f"calling the whisper api {self.api_endpoint} returned a non-success code: {response.status_code}"
        logger.debug(response.json())
        return response.json()['id']

    def check_jobs(self, job_results: dict):
        start_time = time.time()
        all_completed = False
        while not all_completed and (time.time() - start_time) <= self.timeout:
            all_completed = True
            for job_id in job_results:
                if job_results[job_id]: continue
                all_completed = False  # at least one not ready
                try: job_results[job_id] = self.check_job(job_id)
                except Exception as e:
                    logger.error(f"Failed to check {job_id=} with error {e}\n{traceback.format_exc()}")
            if not all_completed: time.sleep(3)
        return job_results

    def check_job(self, job_id):
        r = requests.get(f'{self.api_endpoint}/jobs/{job_id}', headers={'Authorization': f'Bearer {self.api_key}'})
        assert r.status_code == 200, f"Job status did not respond with 200, instead with: {r.status_code}"
        j = r.json()
        logger.debug(f"Checked job {job_id=} with status='{j['status']}'")
        if j['status'] == "processing": return False
        elif j['status'] == "error": return f"Error: {j['meta']['error']}"
        elif j['status'] == "success":
            r_res = requests.get(f'{self.api_endpoint}/jobs/{job_id}/artifacts', headers={'Authorization': f'Bearer {self.api_key}'})
            assert r_res.status_code == 200, f"Job artifacts did not respond with 200, instead with: {r_res.status_code}"
            logger.success(r_res.json())
            return [artifact.get("data").get("text", "") for artifact in r_res.json()]
        return False

    def _get_s3_storage(self) -> S3Storage:
        try:
            return next(s for s in ArchivingContext.get("storages") if s.__class__ == S3Storage)
        except:
            logger.warning("No S3Storage instance found in storages")
            return
