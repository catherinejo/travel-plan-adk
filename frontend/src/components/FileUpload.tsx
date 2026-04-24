import { DragEvent, useRef, useState } from "react";

type FileUploadProps = {
  onFileSelected: (file: File) => void;
};

export function FileUpload({ onFileSelected }: FileUploadProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [fileName, setFileName] = useState<string>("");
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setIsDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file) {
      setFileName(file.name);
      onFileSelected(file);
    }
  };

  return (
    <section>
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setIsDragging(true);
        }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        style={{
          border: `2px dashed ${isDragging ? "#2563eb" : "#9ca3af"}`,
          borderRadius: "10px",
          padding: "24px",
          textAlign: "center",
          cursor: "pointer"
        }}
      >
        <p>파일을 드래그앤드롭하거나 클릭해서 업로드하세요.</p>
        <small>{fileName ? `선택된 파일: ${fileName}` : "아직 선택된 파일이 없습니다."}</small>
      </div>
      <input
        ref={inputRef}
        type="file"
        style={{ display: "none" }}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (!file) {
            return;
          }
          setFileName(file.name);
          onFileSelected(file);
        }}
      />
    </section>
  );
}
