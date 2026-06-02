import { MessageCircle, Monitor, Smartphone, Volume2 } from "lucide-react";
import React from "react";
import type { NotificationChannelId } from "../api/types";
import type { BadgeTone } from "../ui/primitives";
export const notificationChannelMeta: Record<NotificationChannelId, {
  label: string;
  icon: React.ElementType;
  tone: BadgeTone;
  description: string;
}> = {
  mobile: {
    label: "Mobile Notification",
    icon: Smartphone,
    tone: "blue",
    description: "Apprise or Home Assistant mobile app delivery."
  },
  in_app: {
    label: "In-App Notification",
    icon: Monitor,
    tone: "green",
    description: "Realtime dashboard alert for signed-in users."
  },
  voice: {
    label: "Voice Notification",
    icon: Volume2,
    tone: "amber",
    description: "Home Assistant TTS announcement to media players."
  },
  discord: {
    label: "Discord Notification",
    icon: MessageCircle,
    tone: "purple",
    description: "Discord embed delivery to selected channels."
  },
  whatsapp: {
    label: "WhatsApp Message",
    icon: MessageCircle,
    tone: "green",
    description: "WhatsApp Cloud API delivery to Admin users or dynamic phone-number variables."
  }
};
