# LocalMind AI — Offline LLM-Based Chatbot

An offline AI-powered chatbot that generates human-like responses to natural-language inputs using a locally hosted Large Language Model (LLM), without requiring an internet connection.

## Overview

LocalMind AI is designed to provide AI-powered conversational assistance in environments where internet connectivity is unavailable or restricted.

The application runs an LLM locally using Ollama and allows multiple users connected to the same Local Area Network (LAN) to interact with the AI through a web-based chatbot interface.

## Objectives

- Develop an AI-powered natural-language conversational tool.
- Generate human-like responses using a locally hosted LLM.
- Eliminate dependency on cloud-based AI APIs.
- Enable communication between multiple devices over a LAN.
- Maintain conversation context for follow-up questions.
- Ensure functionality without an active internet connection after setup.

## Technology Stack

| Component | Technology |
|---|---|
| Frontend | React.js, Vite |
| Backend | Python, FastAPI |
| LLM Runtime | Ollama |
| AI Model | Qwen 2.5 3B |
| Communication | REST API |
| Network | Local Area Network (LAN) |

## System Architecture

User → React Frontend → FastAPI Backend → Ollama → Qwen 2.5 3B → Generated Response

The AI model and backend run on a central host computer. Other devices connected to the same local network can access the chatbot through a browser.

## Features

- Offline AI-powered conversations
- Natural-language question answering
- Locally hosted LLM inference
- Context-aware follow-up questions
- Conversation history
- Responsive chatbot interface
- Multiple LAN client access
- No dependency on external AI APIs

## Project Structure

```text
localmind-ai/
│
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── App.css
│   │   ├── index.css
│   │   └── main.jsx
│   ├── dist/
│   └── package.json
│
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   └── venv/
│
├── docs/
│   ├── project-report.md
│   ├── architecture.md
│   └── testing.md
│
└── README.md
```

## Installation and Setup

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/localmind-ai.git
cd localmind-ai
```

### 2. Install Ollama

Download Ollama from:

https://ollama.com/download

### 3. Download the AI model

```bash
ollama pull qwen2.5:3b
```

### 4. Set up the backend

```bash
cd backend

python3 -m venv venv

source venv/bin/activate

pip install -r requirements.txt
```

### 5. Set up the frontend

Open another terminal:

```bash
cd frontend

npm install

npm run build
```

### 6. Start the backend

From the backend directory:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

### 7. Access the application

On the host computer:

```text
http://localhost:8000
```

On another device connected to the same LAN:

```text
http://HOST_IP_ADDRESS:8000
```

Replace `HOST_IP_ADDRESS` with the local IP address of the computer hosting the application.

## Offline Functionality

After installing the required software, downloading the model and preparing the frontend build, the application can operate without internet connectivity.

The host computer performs local model inference and serves responses to connected LAN clients.

## Testing

The application should be tested for:

- Local LLM execution
- Natural-language response generation
- Frontend and backend integration
- Conversation context retention
- Multi-device LAN access
- Operation without internet connectivity
- Error handling and recovery

## Limitations

- Response generation depends on the host computer's hardware.
- Model response speed depends on model size and available resources.
- Internet is required for the initial software and model downloads.
- Multiple simultaneous users share the host computer's processing resources.

## Future Enhancements

- Streaming AI responses
- User authentication
- Multiple model selection
- Improved conversation management
- Document-based question answering (RAG)
- Local chat database
- Better concurrent-user handling

## License

This project is licensed under the MIT License.

---

**Developed as an academic project.**

*LocalMind AI — Intelligence that works offline.*
