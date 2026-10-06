import streamlit as st
import requests

st.title(
    "Employee Knowledge Assistant"
)

uploaded = st.file_uploader(
    "Upload File",
    type=["pdf","docx","txt"]
)

if uploaded:

    files = {
        "file":
        (
            uploaded.name,
            uploaded,
            uploaded.type,
        )
    }

    r = requests.post(
        "http://localhost:8000/ingest",
        files=files
    )

    st.success(r.json())

question = st.text_input(
    "Ask a Question"
)

if st.button("Search"):

    r = requests.post(
        "http://localhost:8000/query",
        json={
            "question":
            question
        }
    )

    data = r.json()

    st.subheader("Answer")

    st.write(
        data["answer"]
    )

    st.subheader("Sources")

    for src in data["sources"\]:
        st.write(src)