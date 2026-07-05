"""
Streamlit frontend for the Intelligent Document Processing Pipeline.

Written for: streamlit==1.58.0

Theming note: this app relies entirely on .streamlit/config.toml's explicit
[theme.light] / [theme.dark] tables for color -- no ad hoc st.markdown CSS
overrides here. That keeps theming in one place and means both the light
and dark options in Streamlit's own Settings menu are properly contrasted,
instead of one custom-CSS look bolted on top of Streamlit's default theme.
"""
from __future__ import annotations

import os

import requests
import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")

st.set_page_config(
    page_title="Document Processing Pipeline",
    page_icon="\U0001F4C4",
    layout="wide",
)


def api_get(path: str, **kwargs):
    resp = requests.get(f"{BACKEND_URL}{path}", timeout=30, **kwargs)
    resp.raise_for_status()
    return resp.json()


def api_post_file(path: str, filename: str, content: bytes):
    resp = requests.post(
        f"{BACKEND_URL}{path}",
        files={"file": (filename, content)},
        timeout=180,  # extraction calls the LLM; give it real headroom
    )
    return resp


def api_delete(path: str):
    resp = requests.delete(f"{BACKEND_URL}{path}", timeout=30)
    return resp


def render_extraction(detail: dict) -> None:
    extraction = detail["extraction"]

    col1, col2 = st.columns([2, 1])
    with col1:
        st.subheader("Summary")
        st.write(extraction["summary"])

        st.subheader("Action Items")
        if extraction["action_items"]:
            st.dataframe(
                [
                    {
                        "Description": item["description"],
                        "Owner": item["owner"] or "—",
                        "Due Date": item["due_date"] or "—",
                    }
                    for item in extraction["action_items"]
                ],
                width="stretch",
                hide_index=True,
            )
        else:
            st.caption("No action items detected.")

    with col2:
        st.subheader("Entities")
        if extraction["entities"]:
            st.write(", ".join(extraction["entities"]))
        else:
            st.caption("None detected.")

        st.subheader("Key Dates")
        if extraction["key_dates"]:
            for d in extraction["key_dates"]:
                st.write(f"- {d}")
        else:
            st.caption("None detected.")

        st.subheader("Key Terms")
        if extraction["key_terms"]:
            st.write(", ".join(extraction["key_terms"]))
        else:
            st.caption("None detected.")

    with st.expander("Raw text preview"):
        st.text(detail["text_preview"])


st.title("\U0001F4C4 Intelligent Document Processing Pipeline")
st.caption(
    "Upload a PDF, DOCX, or TXT file to extract a summary, entities, key dates, "
    "key terms, and action items."
)

tab_upload, tab_library = st.tabs(["Upload", "Document Library"])

with tab_upload:
    uploaded = st.file_uploader("Choose a document", type=["pdf", "docx", "txt"])
    if uploaded is not None:
        if st.button("Process document", type="primary"):
            with st.spinner("Parsing and extracting..."):
                response = api_post_file("/documents/upload", uploaded.name, uploaded.getvalue())
            if response.status_code == 200:
                st.success(f"Processed '{uploaded.name}'.")
                render_extraction(response.json())
            else:
                try:
                    detail = response.json().get("detail", response.text)
                except ValueError:
                    detail = response.text
                st.error(f"({response.status_code}) {detail}")

with tab_library:
    try:
        documents = api_get("/documents")
    except requests.RequestException as exc:
        st.error(f"Could not reach the backend at {BACKEND_URL}: {exc}")
        documents = []

    if not documents:
        st.info("No documents processed yet. Upload one in the Upload tab.")
    else:
        for doc in documents:
            label = f"#{doc['id']} · {doc['filename']} · {doc['file_type'].upper()} · {doc['uploaded_at']}"
            with st.expander(label):
                col_a, col_b = st.columns([1, 5])
                with col_a:
                    if st.button("Delete", key=f"delete-{doc['id']}"):
                        api_delete(f"/documents/{doc['id']}")
                        st.rerun()
                with col_b:
                    st.caption(f"{doc['char_count']:,} characters extracted")
                detail = api_get(f"/documents/{doc['id']}")
                render_extraction(detail)

st.sidebar.header("Settings")
st.sidebar.text_input("Backend URL", value=BACKEND_URL, key="backend_url_display", disabled=True)
st.sidebar.caption(
    "Set the BACKEND_URL environment variable to point this app at a "
    "different backend instance."
)
st.sidebar.caption(
    "Use the ⋮ menu in the top-right corner → Settings → Choose app theme "
    "to switch between the light and dark themes."
)
