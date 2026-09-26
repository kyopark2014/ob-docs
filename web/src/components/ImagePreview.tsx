import { useEffect, useState, type DragEvent } from "react";
import { api } from "../api";
import { setVaultMoveDataTransfer } from "./FileTree";

type Props = {
  path: string;
};

export function ImagePreview({ path }: Props) {
  const [failed, setFailed] = useState(false);
  const src = api.rawUrl(path);
  const name = path.split("/").pop() || path;

  useEffect(() => {
    setFailed(false);
  }, [path]);

  const onDragStart = (e: DragEvent<HTMLDivElement>) => {
    // Vault folder moves (same payload as the file panel). Disable native
    // <img> URL/file drag so drops on folders move the vault path.
    setVaultMoveDataTransfer(e.dataTransfer, path, "file");
    e.dataTransfer.setDragImage(e.currentTarget, 40, 24);
  };

  return (
    <div
      className="image-preview-pane"
      draggable={!failed}
      onDragStart={onDragStart}
      title="Drag to a folder in the file panel to move"
    >
      {failed ? (
        <div className="image-preview-error">
          이미지를 불러오지 못했습니다.
          <br />
          <span className="image-preview-name">{name}</span>
        </div>
      ) : (
        <img
          key={path}
          src={src}
          alt={name}
          className="image-preview-img"
          draggable={false}
          onError={() => setFailed(true)}
        />
      )}
    </div>
  );
}
