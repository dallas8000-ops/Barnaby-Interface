import { useEffect } from "react";
import { useApp } from "../store/app";
import { Modal } from "./Modal";
import { TaskBoard } from "./TaskBoard";

/** The task board for screens too narrow for the side panel (a tablet in portrait). */
export function TasksDialog({ onClose }: { onClose: () => void }) {
  const attention = useApp((s) => s.taskAttention);
  const seen = useApp((s) => s.seenTasks);
  useEffect(() => {
    if (attention) seen();
  }, [attention, seen]);
  return (
    <Modal title="Tasks" onClose={onClose} wide>
      <div className="flex max-h-[70vh] min-h-[240px] flex-col">
        <TaskBoard />
      </div>
    </Modal>
  );
}
