import { useApp } from "../store/app";

export function WakeWordStatus() {
  const wakeWordActive = useApp((s) => s.wakeWordActive);
  const toggleWakeWord = useApp((s) => s.toggleWakeWord);
  
  return (
    <div className="flex items-center gap-2">
      <button
        onClick={toggleWakeWord}
        className={`w-2 h-2 rounded-full ${wakeWordActive ? "bg-green-500" : "bg-gray-400"}`}
        aria-label="Toggle wake word"
      />
      <span className="text-sm text-gray-600">
        {wakeWordActive ? "Wake Word Active" : "Wake Word Inactive"}
      </span>
    </div>
  );
}
