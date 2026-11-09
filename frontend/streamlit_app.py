import requests
import streamlit as st


st.set_page_config(page_title="Errand Agent", page_icon="🛠️", layout="wide")
st.title("Errand Agent")
st.caption("Arrange home services with a clear approval step before any change.")

with st.sidebar:
    api_url = st.text_input("Local API", "http://localhost:8000").rstrip("/")
    user_id = st.text_input("User ID", "local-user")
    st.caption("Provider data is simulated and stored locally.")

if "messages" not in st.session_state:
    st.session_state.messages = []
if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = None


@st.fragment(run_every="5s")
def proactive_updates():
    try:
        result = requests.get(f"{api_url}/notifications/{user_id}", timeout=3).json()
        if result.get("notification"):
            st.warning(result["notification"])
    except requests.RequestException:
        st.caption("The local API is unavailable; proactive updates will resume when it is running.")


proactive_updates()
chat_col, trace_col = st.columns([1.2, 1], gap="large")

with chat_col:
    st.subheader("Conversation")
    for item in st.session_state.messages:
        with st.chat_message(item["role"]):
            st.markdown(item["content"])

    prompt = st.chat_input("What would you like to arrange?")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        try:
            response = requests.post(
                f"{api_url}/chat",
                json={"user_id": user_id, "conversation_id": st.session_state.conversation_id, "message": prompt},
                timeout=40,
            )
            response.raise_for_status()
            payload = response.json()
            st.session_state.conversation_id = payload["conversation_id"]
            reply = payload["reply"]
        except requests.RequestException as exc:
            reply = f"I couldn’t reach the local agent API. Check that the backend is running. ({exc})"
        st.session_state.messages.append({"role": "assistant", "content": reply})
        st.rerun()

with trace_col:
    st.subheader("Execution trace")
    if st.session_state.conversation_id:
        try:
            trace = requests.get(f"{api_url}/trace/{st.session_state.conversation_id}", timeout=5).json()["events"]
            if not trace:
                st.info("Trace events will appear as the conversation progresses.")
            for event in reversed(trace):
                with st.expander(f"{event['event']} · {event['run_id']}", expanded=False):
                    st.json(event["payload"])
        except requests.RequestException:
            st.info("Trace is unavailable until the local API is running.")
    else:
        st.info("Start a conversation to see its state transitions, model context and mock tool calls here.")
