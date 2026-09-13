"""Result masking applied immediately before results leave the backend."""

import hashlib

from app.security.models import SecurityContext


class ResultMasker:
    """Apply the configured full, partial, or hash strategy to result rows."""

    def mask(self, rows: list[dict], context: SecurityContext) -> list[dict]:
        masked_rows: list[dict] = []
        for row in rows:
            masked = dict(row)
            for column, strategy in context.masked_columns.items():
                key = next((key for key in masked if key.lower() == column), None)
                if key is not None:
                    masked[key] = self._mask_value(masked[key], strategy)
            masked_rows.append(masked)
        return masked_rows

    @staticmethod
    def _mask_value(value: object, strategy: str) -> object:
        if value is None:
            return None
        if strategy == "full":
            return "******"
        if strategy == "partial":
            text = str(value)
            return text[:1] + "*" * max(1, len(text) - 1)
        if strategy == "hash":
            return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]
        return value
