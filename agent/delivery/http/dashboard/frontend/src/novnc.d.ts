declare module "@novnc/novnc/lib/rfb" {
  type RfbChannel = WebSocket | string;

  export type RfbOptions = {
    shared?: boolean;
    credentials?: { username?: string; password?: string; target?: string };
    wsProtocols?: string[];
  };

  export default class RFB extends EventTarget {
    constructor(target: HTMLElement, urlOrChannel: RfbChannel, options?: RfbOptions);
    viewOnly: boolean;
    scaleViewport: boolean;
    resizeSession: boolean;
    clipViewport: boolean;
    focusOnClick: boolean;
    background: string;
    disconnect(): void;
    focus(): void;
    sendCtrlAltDel(): void;
  }
}
