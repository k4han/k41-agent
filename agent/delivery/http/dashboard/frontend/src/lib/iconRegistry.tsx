import { Box, CloudCog, Cpu } from "lucide-solid";
import type { JSX } from "solid-js";

function TelegramIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M21.5 4.3 18.4 19.6c-.2 1.1-.9 1.4-1.8.9l-5-3.6-2.4 2.3c-.3.3-.5.5-1 .5l.3-4.7 8.5-7.6c.4-.3-.1-.5-.6-.2L5.9 13.4 1.4 12c-1-.3-1-1 .2-1.5L20 4c.9-.3 1.7.2 1.5 1.3z" />
    </svg>
  );
}

function DiscordIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M20.3 4.4A19.7 19.7 0 0 0 15.5 3l-.2.4a17.8 17.8 0 0 0-6.6 0L8.5 3a19.7 19.7 0 0 0-4.8 1.4C1.4 8 .8 11.4 1 14.8a19.9 19.9 0 0 0 5.9 3l1.1-1.7a12.7 12.7 0 0 1-2-1c.2-.1.4-.3.5-.4 3.9 1.8 8.1 1.8 11.9 0 .2.1.3.3.5.4-.6.4-1.3.7-2 1l1.1 1.7a19.9 19.9 0 0 0 6-3c.3-3.9-.4-7.3-2.7-10.4zM8.4 13.1c-1.2 0-2.1-1.1-2.1-2.4 0-1.3.9-2.4 2.1-2.4 1.2 0 2.2 1.1 2.1 2.4 0 1.3-.9 2.4-2.1 2.4zm7.2 0c-1.2 0-2.1-1.1-2.1-2.4 0-1.3.9-2.4 2.1-2.4 1.2 0 2.2 1.1 2.1 2.4 0 1.3-.9 2.4-2.1 2.4z" />
    </svg>
  );
}

function LocalIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z" />
    </svg>
  );
}

function DaytonaIcon() {
  return <CloudCog size={20} />;
}

function ModalIcon() {
  return <Cpu size={20} />;
}

function MicrosandboxIcon() {
  return <Box size={20} />;
}

function FallbackChannelIcon() {
  return <DiscordIcon />;
}

function FallbackBackendIcon() {
  return <LocalIcon />;
}

const CHANNEL_BRAND_ICONS: Record<string, () => JSX.Element> = {
  telegram: TelegramIcon,
  discord: DiscordIcon,
};

const BACKEND_BRAND_ICONS: Record<string, () => JSX.Element> = {
  local: LocalIcon,
  daytona: DaytonaIcon,
  modal: ModalIcon,
  microsandbox: MicrosandboxIcon,
};

export function getChannelIcon(name: string): () => JSX.Element {
  return CHANNEL_BRAND_ICONS[name] ?? FallbackChannelIcon;
}

export function getBackendIcon(name: string): () => JSX.Element {
  return BACKEND_BRAND_ICONS[name] ?? FallbackBackendIcon;
}
