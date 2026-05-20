import DOMPurify from "dompurify";
import { marked } from "marked";
import { createMemo } from "solid-js";

marked.setOptions({
  gfm: true,
  breaks: true,
});

export function Markdown(props: { text: string; class?: string }) {
  const html = createMemo(() => {
    const source = props.text || "";
    const raw = marked.parse(source, { async: false }) as string;
    return DOMPurify.sanitize(raw, { ADD_ATTR: ["target", "rel"] });
  });

  return (
    <div
      class={`markdown ${props.class || ""}`}
      // eslint-disable-next-line solid/no-innerhtml
      innerHTML={html()}
    />
  );
}
