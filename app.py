import json
import os
import re
import uuid
from pathlib import Path

import requests
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv


# Environment configuration
APP_DIRECTORY = Path(__file__).resolve().parent
ENV_FILE = APP_DIRECTORY / ".env"
load_dotenv(dotenv_path=ENV_FILE, override=True)

LYZR_API_KEY = (os.getenv("LYZR_API_KEY") or "").strip()
LYZR_VOICE_AGENT_ID = (
    os.getenv("LYZR_VOICE_AGENT_ID")
    or os.getenv("LYZR_AGENT_ID")
    or ""
).strip()

raw_user_id = (os.getenv("LYZR_USER_ID") or "vidushi-streamlit-user").strip()
LYZR_USER_ID = re.sub(r"[^A-Za-z0-9_-]", "-", raw_user_id)
LYZR_USER_ID = re.sub(r"-+", "-", LYZR_USER_ID).strip("-_")
if not LYZR_USER_ID:
    LYZR_USER_ID = "streamlit-user"

VOICE_API_BASE_URL = "https://voice-livekit.studio.lyzr.ai/v1"
START_SESSION_URL = f"{VOICE_API_BASE_URL}/sessions/start"
END_SESSION_URL = f"{VOICE_API_BASE_URL}/sessions/end"


# Page configuration
st.set_page_config(
    page_title="Enterprise Q&A Voice App",
    page_icon="ðŸŽ™ï¸",
    layout="centered",
)

st.title("Enterprise Q&A Voice App")
st.caption(
    "Ask questions using your microphone and receive grounded answers "
    "from the enterprise knowledge base."
)

if "voice_session" not in st.session_state:
    st.session_state.voice_session = None


def get_headers() -> dict:
    return {
        "x-api-key": LYZR_API_KEY,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def validate_configuration() -> list[str]:
    errors = []

    if not ENV_FILE.exists():
        errors.append(f"The .env file was not found at: {ENV_FILE}")

    if not LYZR_API_KEY:
        errors.append("LYZR_API_KEY is missing from the environment.")

    if not LYZR_VOICE_AGENT_ID:
        errors.append("LYZR_VOICE_AGENT_ID or LYZR_AGENT_ID is missing.")
    elif not re.fullmatch(r"[a-fA-F0-9]{24}", LYZR_VOICE_AGENT_ID):
        errors.append(
            "The Voice Agent ID must be a 24-character Mongo ObjectId."
        )

    if not re.fullmatch(r"[A-Za-z0-9_-]+", LYZR_USER_ID):
        errors.append("The sanitized LYZR_USER_ID is invalid.")

    return errors


def format_api_error(response: requests.Response) -> str:
    try:
        body = json.dumps(response.json())
    except ValueError:
        body = response.text

    return f"{response.request.method} {response.url} returned {response.status_code}: {body}"


def start_voice_session() -> dict:
    room_name = f"enterprise-qa-{uuid.uuid4()}"

    payload = {
        "userIdentity": LYZR_USER_ID,
        "roomName": room_name,
        "agentId": LYZR_VOICE_AGENT_ID,
    }

    response = requests.post(
        START_SESSION_URL,
        headers=get_headers(),
        json=payload,
        timeout=60,
    )

    if not response.ok:
        raise RuntimeError(format_api_error(response))

    session = response.json()
    required_fields = ("userToken", "livekitUrl", "roomName", "sessionId")
    missing = [field for field in required_fields if not session.get(field)]
    if missing:
        raise RuntimeError(
            "The Lyzr session response is missing: " + ", ".join(missing)
        )

    return session


def end_voice_session(session: dict) -> None:
    payloads = []
    if session.get("sessionId"):
        payloads.append({"sessionId": session["sessionId"]})
    if session.get("roomName"):
        payloads.append({"roomName": session["roomName"]})

    last_response = None
    for payload in payloads:
        response = requests.post(
            END_SESSION_URL,
            headers=get_headers(),
            json=payload,
            timeout=30,
        )
        last_response = response
        if response.ok:
            return
        if response.status_code not in (400, 404, 422):
            break

    if last_response is not None:
        raise RuntimeError(format_api_error(last_response))


def create_livekit_component(session: dict) -> str:
    template = r'''<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <script src="https://cdn.jsdelivr.net/npm/livekit-client/dist/livekit-client.umd.min.js"></script>
  <style>
    body { margin: 0; padding: 10px; background: transparent; color: #1f2937;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    .panel { padding: 16px; border: 1px solid #d1d5db; border-radius: 12px;
      background: #f8fafc; }
    .status { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }
    .dot { width: 11px; height: 11px; border-radius: 50%; background: #64748b; }
    .dot.connected { background: #16a34a; }
    .dot.connecting { background: #f59e0b; }
    .dot.error { background: #dc2626; }
    .buttons { display: flex; flex-wrap: wrap; gap: 8px; }
    button { border: 0; border-radius: 8px; padding: 10px 14px;
      font-weight: 600; cursor: pointer; }
    button:disabled { cursor: not-allowed; opacity: .5; }
    #connect { color: white; background: #2563eb; }
    #mute { color: #111827; background: #e5e7eb; }
    #disconnect { color: white; background: #dc2626; }
    #error { margin-top: 12px; color: #b91c1c; font-size: 14px;
      white-space: pre-wrap; }
    #audio { display: none; }
  </style>
</head>
<body>
  <div class="panel">
    <div class="status">
      <div id="dot" class="dot"></div>
      <div id="statusText">Ready to connect</div>
    </div>
    <div class="buttons">
      <button id="connect">Connect microphone</button>
      <button id="mute" disabled>Mute</button>
      <button id="disconnect" disabled>Disconnect</button>
    </div>
    <div id="error"></div>
    <div id="audio"></div>
  </div>

  <script>
    const livekitUrl = __LIVEKIT_URL__;
    const userToken = __USER_TOKEN__;
    const connectButton = document.getElementById("connect");
    const muteButton = document.getElementById("mute");
    const disconnectButton = document.getElementById("disconnect");
    const statusText = document.getElementById("statusText");
    const statusDot = document.getElementById("dot");
    const errorBox = document.getElementById("error");
    const audioContainer = document.getElementById("audio");

    let room = null;
    let microphoneTrack = null;
    let muted = false;

    function updateStatus(message, state = "") {
      statusText.textContent = message;
      statusDot.className = "dot";
      if (state) statusDot.classList.add(state);
    }

    function showError(error) {
      console.error(error);
      errorBox.textContent = error?.message || String(error);
      updateStatus("Connection error", "error");
    }

    function attachAudio(track) {
      const element = track.attach();
      element.autoplay = true;
      audioContainer.appendChild(element);
      element.play().catch(console.warn);
    }

    function resetInterface() {
      audioContainer.querySelectorAll("audio").forEach((element) => element.remove());
      microphoneTrack = null;
      room = null;
      muted = false;
      updateStatus("Disconnected");
      connectButton.disabled = false;
      muteButton.disabled = true;
      disconnectButton.disabled = true;
      muteButton.textContent = "Mute";
    }

    async function disconnectRoom() {
      try {
        if (microphoneTrack) microphoneTrack.stop();
        if (room) await room.disconnect();
      } catch (error) {
        console.error(error);
      } finally {
        resetInterface();
      }
    }

    async function connectRoom() {
      try {
        errorBox.textContent = "";
        updateStatus("Connecting...", "connecting");
        connectButton.disabled = true;

        room = new LivekitClient.Room({ adaptiveStream: true, dynacast: true });
        room.on(LivekitClient.RoomEvent.TrackSubscribed, (track) => {
          if (track.kind === LivekitClient.Track.Kind.Audio) attachAudio(track);
        });
        room.on(LivekitClient.RoomEvent.TrackUnsubscribed, (track) => {
          track.detach().forEach((element) => element.remove());
        });
        room.on(LivekitClient.RoomEvent.Disconnected, resetInterface);

        await room.connect(livekitUrl, userToken, { autoSubscribe: true });
        microphoneTrack = await LivekitClient.createLocalAudioTrack({
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        });
        await room.localParticipant.publishTrack(microphoneTrack);

        updateStatus("Connected â€” you can speak now", "connected");
        muteButton.disabled = false;
        disconnectButton.disabled = false;
      } catch (error) {
        showError(error);
        await disconnectRoom();
      }
    }

    async function toggleMute() {
      if (!microphoneTrack) return;
      try {
        if (muted) {
          await microphoneTrack.unmute();
          muted = false;
          muteButton.textContent = "Mute";
          updateStatus("Connected â€” microphone active", "connected");
        } else {
          await microphoneTrack.mute();
          muted = true;
          muteButton.textContent = "Unmute";
          updateStatus("Connected â€” microphone muted", "connected");
        }
      } catch (error) {
        showError(error);
      }
    }

    connectButton.addEventListener("click", connectRoom);
    muteButton.addEventListener("click", toggleMute);
    disconnectButton.addEventListener("click", disconnectRoom);
    window.addEventListener("beforeunload", () => {
      if (microphoneTrack) microphoneTrack.stop();
      if (room) room.disconnect();
    });
  </script>
</body>
</html>'''

    return (
        template.replace("__LIVEKIT_URL__", json.dumps(session["livekitUrl"]))
        .replace("__USER_TOKEN__", json.dumps(session["userToken"]))
    )


configuration_errors = validate_configuration()
if configuration_errors:
    st.error("The application configuration is incomplete.")
    for configuration_error in configuration_errors:
        st.write(f"â€¢ {configuration_error}")
    st.info("Correct the local .env file, save it, and restart Streamlit.")
    st.stop()


if st.session_state.voice_session is None:
    st.info(
        "Start a voice session, connect your microphone, and ask a question "
        "about the enterprise documents."
    )

    if st.button("Start voice session", type="primary", use_container_width=True):
        with st.spinner("Creating the secure LiveKit voice session..."):
            try:
                st.session_state.voice_session = start_voice_session()
                st.rerun()
            except requests.RequestException as error:
                st.error(f"Unable to contact the Lyzr Voice API: {error}")
            except Exception as error:
                st.error(str(error))
else:
    session = st.session_state.voice_session
    st.success("Voice agent dispatched successfully.")

    with st.expander("Session details"):
        st.json(
            {
                "Room": session.get("roomName"),
                "Session": session.get("sessionId"),
                "Agent dispatched": session.get("agentDispatched", False),
                "User identity": LYZR_USER_ID,
            }
        )

    components.html(
        create_livekit_component(session),
        height=190,
        scrolling=False,
    )
    st.caption(
        "Your browser may ask for microphone permission. Select Allow to speak "
        "with the agent."
    )

    if st.button("End voice session", type="primary", use_container_width=True):
        with st.spinner("Ending the voice session..."):
            try:
                end_voice_session(session)
                st.session_state.voice_session = None
                st.rerun()
            except requests.RequestException as error:
                st.error(f"Unable to contact the Lyzr Voice API: {error}")
            except Exception as error:
                st.error(str(error))

    if st.button("Clear local session", use_container_width=True):
        st.session_state.voice_session = None
        st.rerun()


st.divider()
st.caption("Enterprise Q&A Voice App Â· Lyzr Voice Agent Â· LiveKit Â· Streamlit")