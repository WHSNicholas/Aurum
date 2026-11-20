# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                              Workflows - Report View                                                 #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Preserve the exact generated financial reports for native desktop inspection after reopening a workspace.

The monthly dashboard file is presentation output beside the PDFs, never an accounting input. Reading it
does not rebuild reports, fetch prices or alter saved files. Complete report models retain exact money and
the theme used when the matching PDFs were generated.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Aurum imports
from aurum.finance.dates import working_month_end, working_month_start
from aurum.finance.reports import Reports, ReportSettings

REPORT_VIEW_FILENAME = ".aurum-report-view.json"
PdfHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


# %% 1. Saved Report View ------------------------------------------------------------------------------------
class ReportViewSnapshot(BaseModel):
    """
    Keep one complete generated month and its presentation together with the four PDF fingerprints.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    generated_on: date
    reports: Reports
    settings: ReportSettings
    pdf_hashes: tuple[PdfHash, PdfHash, PdfHash, PdfHash]

    @model_validator(mode="after")
    def _check_period(self) -> Self:
        """
        Reject mixed months or cutoff dates rather than displaying a partly combined report result.
        """
        year, month = map(int, self.month.split("-"))
        start = working_month_start(year, month)
        end = working_month_end(year, month)
        cutoff = self.reports.balance_sheet.as_of
        if not start <= cutoff <= end or any((
            self.reports.investment_report.as_of != cutoff,
            self.reports.income_statement.start_date != start,
            self.reports.income_statement.end_date != cutoff,
            self.reports.budget_report.start_date != start,
            self.reports.budget_report.end_date != cutoff,
        )):
            raise ValueError("Saved dashboard reports must share one reporting month and cutoff")
        return self


def create_report_view(
    reports: Reports, settings: ReportSettings, month: str, generated_on: date,
    documents: tuple[bytes, bytes, bytes, bytes],
) -> ReportViewSnapshot:
    """
    Bind the exact rendered PDF bytes to their validated financial models before publication begins.
    """
    if len(documents) != 4 or any(not content.startswith(b"%PDF-") for content in documents):
        raise ValueError("The report view requires four valid rendered PDF documents")
    return ReportViewSnapshot(
        month=month, generated_on=generated_on, reports=reports, settings=settings,
        pdf_hashes=tuple(sha256(content).hexdigest() for content in documents),
    )


def load_report_view(folder: Path, month: str) -> tuple[ReportViewSnapshot | None, str]:
    """
    Read the last generated dashboard result, distinguishing invalid data from changed PDF output.

    A valid dashboard remains inspectable if a PDF is missing or externally replaced, with a warning that
    it no longer certifies those output files. Unknown schema versions require explicit regeneration.
    """
    path = Path(folder) / REPORT_VIEW_FILENAME
    try:
        snapshot = ReportViewSnapshot.model_validate_json(path.read_bytes())
        if snapshot.month != month or Path(folder).name != month:
            raise ValueError("Saved dashboard month does not match the selected reporting month")
    except FileNotFoundError:
        return None, "Generate to create this month's dashboard reports and PDFs."
    except (OSError, ValueError):
        return None, "Saved dashboard reports could not be read. Generate to rebuild them."
    # Import here to keep report persistence independent of the generation workflow at module load time.
    from aurum.workflows.reports import report_filenames

    for name, expected in zip(report_filenames(month), snapshot.pdf_hashes):
        try:
            actual = sha256((Path(folder) / name).read_bytes()).hexdigest()
        except OSError:
            actual = None
        if actual != expected:
            return snapshot, (
                "Showing the last generated result. Saved PDFs are missing or changed; "
                "Generate to update both outputs."
            )
    return snapshot, ""
