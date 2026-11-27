"""Build a tested warehouse, then replace the previous snapshot."""
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "var" / "retail.duckdb"


def main() -> int:
    warehouse = ROOT / "warehouse"
    destination = Path(os.environ.get("RETAIL_DB_PATH", DEFAULT_DB)).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="retail-build-", dir=destination.parent) as directory:
        stage = Path(directory) / "retail.duckdb"
        env = {
            **os.environ,
            "RETAIL_DB_PATH": str(stage),
            "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
            "DBT_USE_COLORS": "false",
        }
        # dbt's logger holds an open file on Windows until its process exits.
        result = subprocess.run([
            sys.executable, "-c", "from dbt.cli.main import cli; cli()",
            "build",
            "--project-dir", str(warehouse),
            "--profiles-dir", str(warehouse),
            "--target-path", str(Path(directory) / "target"),
            "--log-path", str(Path(directory) / "logs"),
        ], env=env)
        if result.returncode:
            return result.returncode
        stage.replace(destination)
    print(f"Warehouse ready: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
