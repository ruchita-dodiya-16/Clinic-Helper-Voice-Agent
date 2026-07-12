# Healthcare Helper - Browser Microphone Version

This is the same healthcare-helper flow, but **without SIP / phone calling** for now.

The user talks from the browser microphone UI. LiveKit carries the audio. The agent uses:

- LiveKit room for realtime microphone audio
- Cartesia STT for speech-to-text
- Anthropic Claude Haiku for response and tool use
- Cartesia TTS for speech output
- FastAPI for token/session API and static UI
- SQLite for staff tasks: appointment, callback, escalation
- One shared Python virtual environment only: `.venv`

## Architecture

```text
Browser microphone UI
  -> LiveKit room
  -> LiveKit Agent worker
  -> Cartesia STT
  -> Claude Haiku
  -> Cartesia TTS
  -> Browser audio playback
  -> SQLite dashboard/task storage
```

## Setup

```bash
cd healthcare-helper-mic-livekit-cartesia-anthropic
bash start.sh
```

First run creates `.venv`, installs dependencies, and creates `.env`.

Edit `.env`:

```env
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret
ANTHROPIC_API_KEY=your_anthropic_api_key
CARTESIA_API_KEY=your_cartesia_api_key
```

Then run again:

```bash
bash start.sh
```

Open:

```text
http://127.0.0.1:8000
```

Click **Start voice session** and allow microphone access.

## Environment

No SIP values are needed in this version.

```env
AGENT_NAME=healthcare-helper-agent
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
CARTESIA_STT_MODEL=ink-2
CARTESIA_TTS_MODEL=sonic-3
CARTESIA_TTS_VOICE=f786b574-daa5-4673-aa0c-cbe3e8534c02
CARTESIA_TTS_SPEED=1.08
```

## Safety behavior

The agent is a helper, not a doctor replacement.

Allowed:

- scheduling
- rescheduling / cancellation requests
- clinic hours / location / non-clinical FAQs
- callback requests
- retention / service reminders

Escalate or block:

- diagnosis
- medication advice
- dosage guidance
- severe symptoms
- emergency issues
- prescription guidance

## Logs

```bash
tail -f logs/api.log logs/agent.log
```

## Common issues

### Agent does not join

Check `.env` and make sure `AGENT_NAME` matches in both API and worker. It should be:

```env
AGENT_NAME=healthcare-helper-agent
```

### Browser cannot hear agent

Make sure the browser has microphone permission and the tab is not muted. Because the session starts from a click, browser autoplay should usually work.

### Python dependency failure

Use Python 3.11:

```bash
rm -rf .venv
PYTHON_BIN=python3.11 bash start.sh
```
