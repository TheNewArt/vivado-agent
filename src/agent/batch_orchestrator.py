"""
Batch orchestrator for multi-project parallel execution.

Supports:
  - Multi-project queue with priority scheduling
  - Parallel project execution (configurable parallelism)
  - Result aggregation and reporting
  - Local LLM mode (no API calls) for data-compliant scenarios
"""

import time
import json
import threading
from pathlib import Path
from dataclasses import dataclass, field
from queue import PriorityQueue
from enum import Enum
from src.utils.logger import setup_logger
from src.core.config import Config

logger = setup_logger("batch_orchestrator")


class JobPriority(Enum):
    LOW = 3
    NORMAL = 2
    HIGH = 1
    CRITICAL = 0


@dataclass
class Job:
    project_dir: str | Path
    top_module: str = ""
    priority: JobPriority = JobPriority.NORMAL
    run_simulation: bool = True
    run_debug: bool = False
    id: str = ""
    result: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = f"job_{int(time.time() * 1000)}"
        self.project_dir = str(Path(self.project_dir).resolve())

    def __lt__(self, other):
        return self.priority.value < other.priority.value


class BatchOrchestrator:
    """Orchestrate multiple project analyses with priority queue."""

    def __init__(self, config: Config, max_parallel: int = 2):
        self.config = config
        self.max_parallel = max_parallel
        self.queue: PriorityQueue = PriorityQueue()
        self.results: list[dict] = []
        self._lock = threading.Lock()

    def add_job(self, job: Job):
        self.queue.put(job)
        logger.info(f"Added job {job.id}: {job.project_dir} (priority={job.priority.name})")

    def add_jobs(self, jobs: list[Job]):
        for j in jobs:
            self.add_job(j)

    def run_all(self) -> list[dict]:
        """Process queue with parallel workers."""
        threads = []
        for _ in range(min(self.max_parallel, self.queue.qsize())):
            t = threading.Thread(target=self._worker, daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        return self.results

    def _worker(self):
        while not self.queue.empty():
            try:
                job = self.queue.get_nowait()
            except Exception:
                break

            logger.info(f"Processing {job.id}: {job.project_dir}")
            result = self._process_job(job)
            with self._lock:
                self.results.append(result)
            self.queue.task_done()

    def _process_job(self, job: Job) -> dict:
        """Process a single job with full pipeline."""
        from src.agent.simulation_optimizer import SimulationOptimizerAgent
        from src.core.tcl_engine import TCLEngine

        agent = SimulationOptimizerAgent(self.config)
        engine = TCLEngine(self.config.vivado_path)

        project_dir = Path(job.project_dir)
        try:
            project_files = agent.auto_detect(project_dir)
            top = job.top_module or project_files.top_module or "top"

            plan = agent.optimize_simulation(top, str(project_dir),
                                             project_files.tb_files[0] if project_files.tb_files else None,
                                             project_files)

            if plan.get("abort", {}).get("abort"):
                return {"job_id": job.id, "status": "aborted",
                        "reason": plan["abort"]["reason"],
                        "project": job.project_dir}

            if job.run_simulation:
                script = agent.generate_simulation_script(plan)
                sim_result = engine.run_script(script)
                sim_result["errors"] = engine.extract_errors(
                    sim_result.get("stdout", "") + sim_result.get("stderr", ""))
                agent.record_result(plan, sim_result)
                plan["simulation"] = {
                    "elapsed_s": sim_result.get("elapsed", 0),
                    "error_count": len(sim_result.get("errors", [])),
                }
                plan["simulation"]["passed"] = plan["simulation"]["error_count"] == 0

            return {"job_id": job.id, "status": "completed",
                    "project": job.project_dir,
                    "plan": plan}

        except Exception as e:
            logger.error(f"Job {job.id} failed: {e}")
            return {"job_id": job.id, "status": "failed",
                    "project": job.project_dir, "error": str(e)}

    def export_results(self, path: str | Path = "batch_results.json"):
        """Export results to JSON."""
        with open(path, "w") as f:
            json.dump(self.results, f, indent=2, default=str)
        logger.info(f"Results exported to {path}")


def add_local_llm_option(config: Config) -> Config:
    """Override LLM config for local/offline mode.

    Sets enabled=False to skip all LLM calls.
    Useful when processing sensitive design data that cannot be sent to external APIs.
    """
    config.data.setdefault("llm", {})
    config.data["llm"]["enabled"] = False
    logger.info("LLM set to offline mode — no API calls will be made")
    return config