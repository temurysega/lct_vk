import type { Draft } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

export async function api<T>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, { credentials: "same-origin", ...options });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError("Нет соединения с сервером. Попробуйте ещё раз.", 0);
  }
  if (response.status === 204) return undefined as T;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      typeof data?.detail === "string"
        ? data.detail
        : response.status === 422
          ? "Проверьте заполнение полей и допустимые форматы файлов."
          : "Не удалось выполнить запрос. Попробуйте ещё раз.";
    if (response.status === 401 && url.startsWith("/v1/"))
      window.dispatchEvent(new Event("predel:unauthorized"));
    throw new ApiError(message, response.status);
  }
  return data as T;
}

export function saveDraft(draft: Draft) {
  try {
    sessionStorage.setItem("predel:draft", JSON.stringify(draft));
  } catch {
    /* Private browser storage may be disabled. */
  }
}
export function readDraft(): Draft {
  try {
    const value = JSON.parse(sessionStorage.getItem("predel:draft") || "null");
    if (value && typeof value.content === "string")
      return {
        content: value.content,
        count: Math.max(3, Math.min(100, Number(value.count) || 10)),
      };
  } catch {
    /* Fall back to an empty brief. */
  }
  return { content: "", count: 10 };
}
export function pendingJob(
  userId: string,
  value?: string | null,
): string | null {
  try {
    const key = "predel:job:" + userId;
    if (value === null) sessionStorage.removeItem(key);
    else if (value !== undefined) sessionStorage.setItem(key, value);
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}
