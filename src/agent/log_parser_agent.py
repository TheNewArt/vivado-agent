from pathlib import Path
from src.utils.logger import setup_logger
from src.tools.log_analyzer import LogAnalyzer
from src.tools.rag_index import RAGIndex, BugDatabase

logger = setup_logger("log_parser_agent")


class LogParserAgent:
    """
    Phase 2 agent: semantic analysis of Vivado logs.
    Maps tool errors -> source code blocks + fix suggestions via RAG.
    """

    def __init__(self, rtl_dir: str | Path | None = None):
        self.rtl_dir = Path(rtl_dir) if rtl_dir else None
        self.analyzer = LogAnalyzer()
        self.rag = RAGIndex(rtl_dir)
        self.bug_db = BugDatabase()

    def analyze(self, log_path: str | Path) -> dict:
        """Analyze log with RAG-enhanced context."""
        log_path = Path(log_path)
        if not log_path.exists():
            return {"error": f"Log file not found: {log_path}"}

        text = log_path.read_text(encoding="utf-8", errors="replace")
        errors = self.analyzer.parse_errors(text)
        timing = self.analyzer.extract_timing_violations(text)
        summary = self.analyzer.summarize(errors, timing)

        # Build RAG index on demand
        if self.rag and not self.rag._built:
            self.rag.build(self.rtl_dir)

        # Enrich each error with source context and bug DB matches
        enriched_errors = []
        for err in errors:
            enriched = {
                "category": err.category,
                "severity": err.severity,
                "line_no": err.line_no,
                "message": err.message,
                "suggestion": err.suggestion,
            }
            # RAG: find enclosing code block
            if self.rag._built and err.line_no:
                for file_key in self.rag.get_all_file_keys():
                    block = self.rag.lookup(file_key, err.line_no)
                    if block:
                        enriched["source_block"] = block.content[:300]
                        enriched["source_file"] = block.file_path
                        enriched["module"] = block.module
                        break

            # Bug DB match
            db_matches = self.bug_db.match(err.message)
            if db_matches:
                enriched["bug_db_matches"] = db_matches

            enriched_errors.append(enriched)

        logger.info(f"Log analysis: {len(enriched_errors)} errors, {len(timing)} timing violations")
        return {
            "log_path": str(log_path),
            "errors": enriched_errors,
            "timing_violations": timing,
            "summary": summary,
            "has_errors": len(enriched_errors) > 0,
        }