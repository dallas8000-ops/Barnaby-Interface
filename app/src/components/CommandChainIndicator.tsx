import { useApp } from "../store/app";

export function CommandChainIndicator() {
  const commandChainActive = useApp((s) => s.commandChainActive);
  const commandChainDepth = useApp((s) => s.commandChainDepth);
  
  if (!commandChainActive) {
    return null;
  }
  
  return (
    <div className="flex items-center gap-2 px-3 py-1 bg-purple-100 text-purple-700 rounded text-sm">
      <span>Chain Active</span>
      <span className="bg-purple-200 px-2 rounded">
        {commandChainDepth} commands
      </span>
    </div>
  );
}
