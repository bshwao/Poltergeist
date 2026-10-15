const configuredUrl = import.meta.env.VITE_API_URL?.trim() || "http://localhost:8000";
const parsedUrl = new URL(configuredUrl);

if (!["http:", "https:"].includes(parsedUrl.protocol)) {
  throw new Error("VITE_API_URL must be an absolute HTTP or HTTPS URL.");
}

export const API_BASE_URL = configuredUrl.replace(/\/+$/, "");
