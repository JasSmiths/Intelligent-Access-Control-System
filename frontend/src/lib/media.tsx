import type { UserAccount } from "../api/types";
import { displayUserName, userInitials } from "./format";
export function mediaSource(url?: string | null, dataUrl?: string | null, variant: "thumb" | "full" = "full") {
  if (!url) return dataUrl || "";
  if (variant === "full") return url;
  return mediaVariantUrl(url, variant);
}
export function mediaVariantUrl(url: string, variant: "thumb" | "full") {
  if (variant === "full") return url;
  return `${url}${url.includes("?") ? "&" : "?"}variant=${variant}`;
}
export function UserAvatar({ user, size = "normal" }: { user: UserAccount; size?: "normal" | "large" }) {
  const imageSource = mediaSource(user.profile_photo_url, user.profile_photo_data_url, "thumb");
  return (
    <span className={size === "large" ? "profile-photo large" : "profile-photo"} aria-label={displayUserName(user)}>
      {imageSource ? <img alt="" decoding="async" loading="lazy" src={imageSource} /> : userInitials(user)}
    </span>
  );
}
export function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("Unable to read profile image"));
    reader.readAsDataURL(file);
  });
}
