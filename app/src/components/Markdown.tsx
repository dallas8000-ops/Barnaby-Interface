import { Check, Copy } from "lucide-react";
import { memo, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { openExternal } from "../lib/platform";

function CodeBlock({ children, lang }: { children: string; lang: string | undefined }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="group relative my-2 rounded-md border border-line bg-raised">
      <div className="flex items-center justify-between border-b border-line px-3 py-1 text-xs text-muted">
        <span className="font-strip">{lang ?? "code"}</span>
        <button
          type="button"
          aria-label="Copy code"
          className="inline-flex items-center gap-1 hover:text-ink"
          onClick={() => {
            void navigator.clipboard?.writeText(children).then(() => {
              setCopied(true);
              setTimeout(() => setCopied(false), 1200);
            });
          }}
        >
          {copied ? <Check size={12} aria-hidden /> : <Copy size={12} aria-hidden />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="scroll-quiet overflow-x-auto px-3 py-2">
        <code>{children}</code>
      </pre>
    </div>
  );
}

const components: Components = {
  pre: ({ children }) => <>{children}</>,
  code: ({ className, children }) => {
    const text = String(children ?? "");
    const lang = /language-([\w+-]+)/.exec(className ?? "")?.[1];
    // Block code arrives with a language class or a trailing newline.
    if (lang || text.includes("\n")) return <CodeBlock lang={lang}>{text.replace(/\n$/, "")}</CodeBlock>;
    return <code>{children as ReactNode}</code>;
  },
  // Model output must never navigate the app window. Only http(s) links open,
  // and in the desktop shell they go to the system browser via the opener plugin.
  a: ({ href, children }) => {
    const safe = typeof href === "string" && /^https?:\/\//i.test(href);
    return (
      <a
        href={safe ? href : undefined}
        target="_blank"
        rel="noreferrer noopener"
        onClick={(e) => {
          e.preventDefault();
          if (safe) void openExternal(href);
        }}
      >
        {children}
      </a>
    );
  },
  img: () => null, // no remote image loads from model output
};

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="prose-cab">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} skipHtml>
        {text}
      </ReactMarkdown>
    </div>
  );
});
