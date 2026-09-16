const SHOW_IMAGES_KEY = "ob-docs:view-show-images";

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|ico|bmp|heic|avif)$/i;

export function isImageFileName(name: string): boolean {
  return IMAGE_EXT.test(name);
}

export function getShowImages(): boolean {
  try {
    return localStorage.getItem(SHOW_IMAGES_KEY) === "1";
  } catch {
    return false;
  }
}

export function setShowImages(value: boolean): void {
  try {
    localStorage.setItem(SHOW_IMAGES_KEY, value ? "1" : "0");
  } catch {
    /* ignore */
  }
}
