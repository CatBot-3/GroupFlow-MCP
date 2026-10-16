import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource-variable/recursive/full.css';
import './styles.css';
import { App } from './App';
import { ChatApp } from './chat/ChatApp';

const chat = window.location.pathname.replace(/\/+$/, '') === '/chat';
if (chat) document.title = 'GroupFlow chat';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {chat ? <ChatApp /> : <App />}
  </StrictMode>,
);
