import io
import sys
import tempfile
from pathlib import Path

import streamlit as st

# Make the existing src package importable.
ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from main import run  # noqa: E402


st.set_page_config(
    page_title="PII Redaction Tool",
    page_icon="🔒",
    layout="centered",
)

st.title("🔒 PII Redaction Tool")
st.write(
    "Upload a DOCX document and automatically replace detected "
    "personally identifiable information (PII) with synthetic values."
)

st.info(
    "Supported PII includes names, email addresses, phone numbers, "
    "company names, addresses, SSNs, credit cards, dates of birth, "
    "and IP addresses."
)

uploaded_file = st.file_uploader(
    "Upload a DOCX document",
    type=["docx"],
)

if uploaded_file is not None:
    st.success(f"Selected: {uploaded_file.name}")

    if st.button("Redact PII", type="primary", use_container_width=True):
        with st.spinner("Scanning and redacting the document..."):
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    tmp_path = Path(tmp)

                    input_path = tmp_path / "input.docx"
                    output_path = tmp_path / "redacted.docx"
                    mapping_path = tmp_path / "mapping.json"

                    input_path.write_bytes(uploaded_file.getvalue())

                    # Use the same production pipeline as the CLI.
                    result = run(
                        str(input_path),
                        str(output_path),
                        str(mapping_path),
                    )

                    if not output_path.exists():
                        raise RuntimeError(
                            "The redaction pipeline completed but did not "
                            "produce an output DOCX."
                        )

                    redacted_bytes = output_path.read_bytes()

                    st.success("Redaction completed successfully.")

                    st.download_button(
                        label="⬇️ Download Redacted DOCX",
                        data=redacted_bytes,
                        file_name=f"redacted_{uploaded_file.name}",
                        mime=(
                            "application/vnd.openxmlformats-officedocument."
                            "wordprocessingml.document"
                        ),
                        use_container_width=True,
                    )

                    # Display useful information when the current run()
                    # returns statistics. The application does not depend
                    # on a particular return structure.
                    if isinstance(result, dict):
                        if "pii_instances" in result:
                            st.metric(
                                "PII instances redacted",
                                result["pii_instances"],
                            )
                        elif "instances" in result:
                            st.metric(
                                "PII instances redacted",
                                result["instances"],
                            )

            except Exception as exc:
                st.error("Redaction failed.")
                st.exception(exc)

st.divider()
st.caption(
    "PII Redaction Tool • Regex + NER based detection • "
    "Synthetic replacement values"
)