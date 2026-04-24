type DownloadButtonProps = {
  url: string | null;
  filename?: string;
};

export function DownloadButton({ url, filename = "report.pdf" }: DownloadButtonProps) {
  if (!url) {
    return null;
  }

  return (
    <div style={{ marginTop: "16px" }}>
      <a href={url} download={filename}>
        <button type="button">리포트 다운로드</button>
      </a>
    </div>
  );
}
