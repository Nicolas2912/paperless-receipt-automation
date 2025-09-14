from dataclasses import dataclass


@dataclass
class ExtractedMetadata:
    korrespondent: str
    ausstellungsdatum: str
    betrag_value: str
    betrag_currency: str
    dokumenttyp: str = "Kassenbon"

    def title(self) -> str:
        """Return title in format:
        "<date> - <korrespondent> - <betrag_value_de>"
        where amount is German-formatted (dot thousands, comma decimals).
        """
        def _fmt_de(x: str) -> str:
            from decimal import Decimal
            try:
                val = Decimal(str(x).replace(",", "."))
                s = f"{val:,.2f}"
                return s.replace(",", "_").replace(".", ",").replace("_", ".")
            except Exception:
                return str(x)

        return f"{self.ausstellungsdatum} - {self.korrespondent} - {_fmt_de(self.betrag_value)}"

