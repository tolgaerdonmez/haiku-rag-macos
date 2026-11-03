import { appleScriptString } from "./skim";

/** Opens the document in Word and moves the insertion point to the page. */
export function wordScript(path: string, page: number | undefined): string {
  const goTo = page
    ? `
  try
    set r to go to active document what go to a page which go absolute count ${page}
    select r
  end try`
    : "";
  return `tell application "Microsoft Word"
  activate
  open POSIX file ${appleScriptString(path)}${goTo}
end tell`;
}

/** Opens the presentation in PowerPoint and shows the slide. */
export function powerPointScript(path: string, slide: number | undefined): string {
  const goTo = slide
    ? `
  try
    go to slide (view of document window 1) number ${slide}
  end try`
    : "";
  return `tell application "Microsoft PowerPoint"
  activate
  open POSIX file ${appleScriptString(path)}${goTo}
end tell`;
}
