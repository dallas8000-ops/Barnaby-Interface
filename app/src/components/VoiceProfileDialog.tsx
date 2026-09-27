import { Trash2 } from "lucide-react";
import { useEffect } from "react";
import { send } from "../lib/client";
import { useApp } from "../store/app";
import { Modal } from "./Modal";

export function VoiceProfileDialog({ onClose }: { onClose: () => void }) {
  const profile = useApp((s) => s.voiceProfile);

  useEffect(() => {
    send({ type: "get_voice_profile" });
  }, []);

  return (
    <Modal title="Voice profile" onClose={onClose} wide>
      <p className="text-sm text-muted">
        When you fix a word in the review box, Barnaby remembers it. After the same fix twice it corrects that phrase
        automatically and listens for the word.
      </p>

      <section className="mt-4">
        <h3 className="font-strip text-base font-semibold">Corrections</h3>
        {!profile ? (
          <p className="mt-2 text-sm text-muted">Loading…</p>
        ) : profile.corrections.length === 0 ? (
          <p className="mt-2 text-sm text-muted">No corrections yet. They appear after you edit a transcript before sending it.</p>
        ) : (
          <table className="mt-2 w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted">
                <th className="py-1 font-medium">I heard</th>
                <th className="py-1 font-medium">You meant</th>
                <th className="py-1 font-medium">Times</th>
                <th className="py-1 font-medium">Active</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {profile.corrections.map((c) => {
                const active = c.count >= 2 && !c.disabled;
                return (
                  <tr key={`${c.heard}→${c.meant}`} className="border-t border-line">
                    <td className="py-1.5 text-muted">{c.heard}</td>
                    <td className="py-1.5 font-medium">{c.meant}</td>
                    <td className="py-1.5 tabular-nums">{c.count}</td>
                    <td className="py-1.5">
                      <input
                        type="checkbox"
                        aria-label={`Auto-correct “${c.heard}” to ${c.meant}`}
                        checked={!c.disabled}
                        onChange={(e) =>
                          send({ type: "update_correction", heard: c.heard, meant: c.meant, action: e.target.checked ? "enable" : "disable" })
                        }
                        className="accent-[var(--c-cyan)]"
                      />
                      {!active && !c.disabled && <span className="ml-1.5 text-xs text-muted">after one more</span>}
                    </td>
                    <td className="py-1.5 text-right">
                      <button
                        type="button"
                        aria-label={`Delete correction ${c.heard}`}
                        onClick={() => send({ type: "update_correction", heard: c.heard, meant: c.meant, action: "delete" })}
                        className="text-muted hover:text-red"
                      >
                        <Trash2 size={14} aria-hidden />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      {profile && (
        <>
          <section className="mt-5">
            <h3 className="font-strip text-base font-semibold">Words Barnaby listens for</h3>
            <p className="mt-1 text-xs text-muted">From your corrections and the names you use often.</p>
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {profile.vocabulary.length === 0 && <li className="text-sm text-muted">None yet.</li>}
              {profile.vocabulary.map((term) => (
                <li key={term} className="inline-flex items-center gap-1 rounded-full border border-line bg-raised py-0.5 pl-2.5 pr-1 text-sm">
                  {term}
                  <button type="button" aria-label={`Remove ${term}`} onClick={() => send({ type: "delete_vocab", term })} className="rounded-full px-1 text-muted hover:text-red">
                    ×
                  </button>
                </li>
              ))}
            </ul>
          </section>
          <section className="mt-5">
            <h3 className="font-strip text-base font-semibold">From this project</h3>
            <p className="mt-1 text-xs text-muted">Read from package.json, pyproject.toml, requirements.txt and similar files.</p>
            <p className="mt-2 text-sm text-ink">{profile.projectTerms.length ? profile.projectTerms.join(", ") : "No dependency files found."}</p>
          </section>
          <p className="mt-5 border-t border-line pt-3 text-xs text-muted">
            Voice samples saved for fine-tuning: <span className="tabular-nums text-ink">{profile.samples}</span>. Turn this on in Settings.
          </p>
        </>
      )}
    </Modal>
  );
}
