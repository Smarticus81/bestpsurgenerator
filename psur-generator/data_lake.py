"""Unified Data Lake — loads all parsed PSUR data into a queryable SQLite store.

Enables cross-source joins that were previously impossible:
  - Complaints joined to sales regions
  - CAPAs linked to complaints by date proximity
  - Temporal filtering across all data sources

Usage:
    lake = DataLake(parsed_data, device_context, stats_dict)
    results = lake.query("SELECT region, COUNT(*) FROM complaints GROUP BY region")
    summary = lake.ask("complaints by region with sales denominator")
"""

from __future__ import annotations
import json
import logging
import sqlite3
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class DataLake:
    """In-memory SQLite data lake for cross-source PSUR queries."""

    def __init__(
        self,
        parsed_data: Dict[str, Any],
        device_context: Dict[str, Any],
        stats: Dict[str, Any],
    ):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._device_context = device_context
        self._stats = stats
        self._create_tables()
        self._load_data(parsed_data, stats)

    def _create_tables(self) -> None:
        """Create the unified schema."""
        cur = self.conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_date TEXT,
                year INTEGER,
                month INTEGER,
                region TEXT,
                country TEXT,
                product TEXT,
                units INTEGER,
                source_file TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS complaints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                complaint_number TEXT,
                event_date TEXT,
                year INTEGER,
                month INTEGER,
                region TEXT,
                country TEXT,
                imdrf_code TEXT,
                harm_category TEXT,
                is_serious INTEGER DEFAULT 0,
                description TEXT,
                source_file TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS capas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                capa_number TEXT,
                title TEXT,
                status TEXT,
                open_date TEXT,
                close_date TEXT,
                root_cause TEXT,
                capa_type TEXT,
                source_file TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS fscas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fsca_id TEXT,
                title TEXT,
                status TEXT,
                initiation_date TEXT,
                completion_date TEXT,
                source_file TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS statistics (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS device (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # Indexes for common query patterns
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(event_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_sales_region ON sales(region)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_complaints_date ON complaints(event_date)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_complaints_imdrf ON complaints(imdrf_code)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_complaints_region ON complaints(region)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_capas_status ON capas(status)")

        self.conn.commit()

    def _load_data(self, parsed_data: Dict[str, Any], stats: Dict[str, Any]) -> None:
        """Load all parsed data into tables."""
        cur = self.conn.cursor()

        # ── Sales ────────────────────────────────────────────────────────
        sales = parsed_data.get("sales") or {}
        source_file = sales.get("source_file", "")
        by_month = sales.get("by_month", {})
        by_region = sales.get("by_region", {})

        # Load monthly totals
        for month_key, units in by_month.items():
            parts = month_key.split("-")
            year = int(parts[0]) if len(parts) >= 1 else 0
            month = int(parts[1]) if len(parts) >= 2 else 0
            cur.execute(
                "INSERT INTO sales (event_date, year, month, units, source_file) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"{month_key}-01", year, month, units, source_file)
            )

        # Load regional totals
        for region, units in by_region.items():
            cur.execute(
                "INSERT INTO sales (region, units, source_file) VALUES (?, ?, ?)",
                (region, units, source_file)
            )

        # ── Complaints ───────────────────────────────────────────────────
        complaints = parsed_data.get("complaints") or {}
        source_file = complaints.get("source_file", "")
        for summary in complaints.get("complaint_summaries", []):
            event_date = summary.get("date", "")
            parts = event_date.split("-") if event_date else []
            year = int(parts[0]) if len(parts) >= 1 and parts[0].isdigit() else None
            month = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else None
            cur.execute(
                "INSERT INTO complaints "
                "(complaint_number, event_date, year, month, region, country, "
                "imdrf_code, harm_category, is_serious, description, source_file) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    summary.get("complaint_number", ""),
                    event_date,
                    year,
                    month,
                    summary.get("region", ""),
                    summary.get("country", ""),
                    summary.get("imdrf_code", ""),
                    summary.get("harm", ""),
                    1 if summary.get("serious") else 0,
                    summary.get("description", "")[:500],
                    source_file,
                )
            )

        # ── CAPAs ────────────────────────────────────────────────────────
        capa_data = parsed_data.get("capa") or {}
        if isinstance(capa_data, dict):
            source_file = capa_data.get("source_file", "")
            for rec in capa_data.get("capa_records", []):
                cur.execute(
                    "INSERT INTO capas "
                    "(capa_number, title, status, open_date, close_date, "
                    "root_cause, capa_type, source_file) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        rec.get("capa_number", ""),
                        rec.get("title", ""),
                        rec.get("status", ""),
                        rec.get("open_date", ""),
                        rec.get("close_date", ""),
                        rec.get("root_cause", ""),
                        rec.get("type", ""),
                        source_file,
                    )
                )

        # ── FSCAs ────────────────────────────────────────────────────────
        fsca_data = parsed_data.get("fsca") or []
        if isinstance(fsca_data, dict):
            fsca_rows = fsca_data.get("records") or fsca_data.get("fsca_records") or []
        else:
            fsca_rows = fsca_data
        for fsca in (fsca_rows or []):
            if isinstance(fsca, dict):
                cur.execute(
                    "INSERT INTO fscas "
                    "(fsca_id, title, status, initiation_date, completion_date, "
                    "source_file) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        fsca.get("fsca_id") or fsca.get("reference_number") or fsca.get("action_id", ""),
                        fsca.get("title") or fsca.get("description") or fsca.get("reason", ""),
                        fsca.get("status", ""),
                        fsca.get("initiation_date") or fsca.get("date_initiated") or fsca.get("date", ""),
                        fsca.get("completion_date", ""),
                        "fsca.csv",
                    )
                )

        # ── Statistics (key-value store) ─────────────────────────────────
        def _flatten_stats(obj, prefix=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _flatten_stats(v, f"{prefix}.{k}" if prefix else k)
            elif isinstance(obj, (list, tuple)):
                cur.execute(
                    "INSERT OR REPLACE INTO statistics (key, value) VALUES (?, ?)",
                    (prefix, json.dumps(obj))
                )
            else:
                cur.execute(
                    "INSERT OR REPLACE INTO statistics (key, value) VALUES (?, ?)",
                    (prefix, str(obj) if obj is not None else "")
                )

        _flatten_stats(stats)

        # ── Device context ───────────────────────────────────────────────
        def _flatten_device(obj, prefix=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    _flatten_device(v, f"{prefix}.{k}" if prefix else k)
            else:
                cur.execute(
                    "INSERT OR REPLACE INTO device (key, value) VALUES (?, ?)",
                    (prefix, str(obj) if obj is not None else "")
                )

        _flatten_device(self._device_context)

        self.conn.commit()
        logger.info(
            f"DataLake loaded: {self._count('sales')} sales, "
            f"{self._count('complaints')} complaints, "
            f"{self._count('capas')} CAPAs, "
            f"{self._count('fscas')} FSCAs"
        )

    def _count(self, table: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # ── Public API ───────────────────────────────────────────────────────

    def query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Execute a SQL query and return results as list of dicts."""
        try:
            rows = self.conn.execute(sql, params).fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"DataLake query failed: {e}\nSQL: {sql}")
            return [{"error": str(e)}]

    def query_text(self, sql: str, params: tuple = ()) -> str:
        """Execute a SQL query and return results as formatted text."""
        results = self.query(sql, params)
        if not results:
            return "No results."
        if "error" in results[0]:
            return f"Query error: {results[0]['error']}"

        # Format as text table
        keys = list(results[0].keys())
        lines = [" | ".join(keys)]
        lines.append("-" * len(lines[0]))
        for row in results[:50]:  # Cap at 50 rows
            lines.append(" | ".join(str(row.get(k, "")) for k in keys))
        return "\n".join(lines)

    def get_schema(self) -> str:
        """Return the database schema as text for agent context."""
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        schema_parts = []
        for (table_name,) in tables:
            cols = self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            count = self._count(table_name)
            col_defs = ", ".join(f"{c[1]} {c[2]}" for c in cols)
            schema_parts.append(f"{table_name} ({count} rows): {col_defs}")
        return "\n".join(schema_parts)

    def summary(self) -> Dict[str, Any]:
        """Return a high-level summary of all data in the lake."""
        return {
            "tables": {
                "sales": {
                    "row_count": self._count("sales"),
                    "regions": [r["region"] for r in self.query(
                        "SELECT DISTINCT region FROM sales WHERE region IS NOT NULL AND region != ''"
                    )],
                    "date_range": self.query(
                        "SELECT MIN(event_date) as min_date, MAX(event_date) as max_date "
                        "FROM sales WHERE event_date IS NOT NULL"
                    ),
                },
                "complaints": {
                    "row_count": self._count("complaints"),
                    "imdrf_codes": [r["imdrf_code"] for r in self.query(
                        "SELECT DISTINCT imdrf_code FROM complaints "
                        "WHERE imdrf_code IS NOT NULL AND imdrf_code != ''"
                    )],
                    "serious_count": self.query(
                        "SELECT COUNT(*) as n FROM complaints WHERE is_serious = 1"
                    )[0]["n"],
                },
                "capas": {
                    "row_count": self._count("capas"),
                    "statuses": {r["status"]: r["n"] for r in self.query(
                        "SELECT status, COUNT(*) as n FROM capas GROUP BY status"
                    )},
                },
                "fscas": {"row_count": self._count("fscas")},
            }
        }

    # ── Pre-built cross-source queries ───────────────────────────────

    def complaints_with_sales_denominator(self) -> List[Dict]:
        """Join complaints to monthly sales to compute per-month rates."""
        return self.query("""
            SELECT
                c.month,
                c.year,
                COUNT(c.id) as complaint_count,
                COALESCE(s.units, 0) as units_sold,
                CASE WHEN s.units > 0
                     THEN ROUND(COUNT(c.id) * 100.0 / s.units, 4)
                     ELSE NULL
                END as rate_pct
            FROM complaints c
            LEFT JOIN sales s ON c.year = s.year AND c.month = s.month
            WHERE c.year IS NOT NULL
            GROUP BY c.year, c.month
            ORDER BY c.year, c.month
        """)

    def capas_near_complaint_spikes(self, threshold: int = 3) -> List[Dict]:
        """Find CAPAs opened within 60 days of complaint spikes."""
        return self.query("""
            WITH monthly_complaints AS (
                SELECT year, month, COUNT(*) as n
                FROM complaints
                GROUP BY year, month
                HAVING COUNT(*) >= ?
            )
            SELECT
                mc.year, mc.month, mc.n as complaints,
                ca.capa_number, ca.title, ca.open_date, ca.status
            FROM monthly_complaints mc
            JOIN capas ca ON ca.open_date BETWEEN
                printf('%04d-%02d-01', mc.year, mc.month) AND
                printf('%04d-%02d-28', mc.year, CASE WHEN mc.month < 11 THEN mc.month + 2 ELSE 12 END)
            ORDER BY mc.year, mc.month
        """, (threshold,))

    def serious_incidents_by_region(self) -> List[Dict]:
        """Serious incidents joined with regional sales denominator."""
        return self.query("""
            SELECT
                c.region,
                COUNT(c.id) as serious_count,
                COALESCE(s.units, 0) as region_units,
                CASE WHEN s.units > 0
                     THEN ROUND(COUNT(c.id) * 100.0 / s.units, 4)
                     ELSE NULL
                END as serious_rate_pct
            FROM complaints c
            LEFT JOIN (
                SELECT region, SUM(units) as units FROM sales
                WHERE region IS NOT NULL GROUP BY region
            ) s ON c.region = s.region
            WHERE c.is_serious = 1
            GROUP BY c.region
            ORDER BY serious_count DESC
        """)

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
