import { createContext, createSignal, For, JSX, useContext } from "solid-js";

type ToastType = "success" | "error" | "warning";

type ToastItem = {
  id: number;
  message: string;
  type: ToastType;
};

type ToastContextValue = {
  showToast: (message: string, type?: ToastType) => void;
};

const ToastContext = createContext<ToastContextValue>();

export function ToastProvider(props: { children: JSX.Element }) {
  const [items, setItems] = createSignal<ToastItem[]>([]);
  let nextId = 1;

  const showToast = (message: string, type: ToastType = "success") => {
    const id = nextId;
    nextId += 1;
    setItems((current) => [...current, { id, message, type }]);
    window.setTimeout(() => {
      setItems((current) => current.filter((item) => item.id !== id));
    }, 3500);
  };

  return (
    <ToastContext.Provider value={{ showToast }}>
      {props.children}
      <div class="toast-region" aria-live="polite">
        <For each={items()}>
          {(item) => <div class={`toast ${item.type}`}>{item.message}</div>}
        </For>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("ToastProvider is missing");
  }
  return context;
}

