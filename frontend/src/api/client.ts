const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export type ReportResponse = {
  downloadUrl: string;
};

type ProgressCallback = (step: number) => void;

export async function requestReport(file: File, onProgress?: ProgressCallback): Promise<ReportResponse> {
  const formData = new FormData();
  formData.append("file", file);

  onProgress?.(2);
  const response = await fetch(`${BASE_URL}/report`, {
    method: "POST",
    body: formData
  });

  if (!response.ok) {
    throw new Error(`요청 실패 (${response.status})`);
  }

  onProgress?.(4);
  const data = (await response.json()) as { download_url?: string; downloadUrl?: string };
  const downloadUrl = data.downloadUrl ?? data.download_url;

  if (!downloadUrl) {
    throw new Error("다운로드 URL을 응답에서 찾지 못했습니다.");
  }

  return { downloadUrl };
}
