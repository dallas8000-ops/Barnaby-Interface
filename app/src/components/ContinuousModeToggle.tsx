import { useApp } from "../store/app";

export function ContinuousModeToggle() {
  const continuousMode = useApp((s) => s.continuousMode);
  const toggleContinuousMode = useApp((s) => s.toggleContinuousMode);
  
  return (
    <div className="flex items-center gap-2">
      <button
        onClick={toggleContinuousMode}
        className={`w-2 h-2 rounded-full ${continuousMode ? "bg-blue-500" : "bg-gray-400"}`}
        aria-label="Toggle continuous mode"
      />
      <span className="text-sm text-gray-600">
        {continuousMode ? "Continuous Mode Active" : "Continuous Mode Inactive"}
      </span>
    </div>
  );
}
