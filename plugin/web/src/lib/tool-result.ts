import type { ImageSource, ToolResultBlock } from "@/lib/types";

/**
 * Recursively replace long base64 `data` strings with a short placeholder so
 * tool-result JSON stays readable when rendered in the chat.
 */
export function redactLargeData(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactLargeData);
  if (!value || typeof value !== "object") return value;

  const record = value as Record<string, unknown>;
  return Object.fromEntries(
    Object.entries(record).map(([key, item]) => [
      key,
      key === "data" && typeof item === "string" && item.length > 120
        ? `[base64 omitted, ${item.length} chars]`
        : redactLargeData(item),
    ]),
  );
}

/** Extract all base64 image sources from a tool-result content array. */
export function getImageSources(
  content: ToolResultBlock["content"],
): ImageSource[] {
  if (!Array.isArray(content)) return [];
  return content.flatMap((item) => {
    if (item.type !== "image") return [];
    const source = item.source;
    if (!isImageSource(source)) return [];
    return [source];
  });
}

/** Type guard for a base64 `ImageSource`. */
export function isImageSource(value: unknown): value is ImageSource {
  if (!value || typeof value !== "object") return false;
  const source = value as Partial<ImageSource>;
  return (
    source.type === "base64" &&
    typeof source.media_type === "string" &&
    typeof source.data === "string"
  );
}
