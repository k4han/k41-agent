import { randomUUID } from 'node:crypto';

/** Serve the native envelope and UI projection used by production v3 streams. */
export function protocolFixture(response, threadId) {
  const write = response.write.bind(response), end = response.end.bind(response);
  const runId = randomUUID();
  let seq = 0, messageId = '';
  const emit = (method, data) => write(JSON.stringify({ type: 'event', method,
    params: { namespace: [], timestamp: Date.now(), data }, seq: ++seq, event_id: `${runId}:${seq}`,
  }) + '\n');
  const message = (data) => emit('messages', [data, { run_id: messageId }]);
  emit('lifecycle', { event: 'started', run_id: runId, thread_id: threadId });
  const project = (chunk) => {
    for (const line of String(chunk).split('\n').filter(Boolean)) {
      const event = JSON.parse(line);
      if (event.type === 'message') {
        if (!messageId) {
          messageId = randomUUID();
          message({ event: 'message-start', role: 'ai', id: messageId });
          message({ event: 'content-block-start', index: 0, content: { type: 'text', text: '' } });
        }
        message({ event: 'content-block-delta', index: 0, delta: { type: 'text-delta', text: event.content } });
      } else if (event.type === 'tool_call') {
        emit('tools', { event: 'tool-started', tool_call_id: event.id, tool_name: event.name, input: event.args });
      }
      emit('custom:k41', { ...event, projection: 'ui' });
    }
    return true;
  };
  response.write = project;
  response.end = (chunk) => {
    if (response.writableEnded || response.destroyed) return response;
    if (chunk) project(chunk);
    emit('lifecycle', { event: 'completed', run_id: runId, thread_id: threadId });
    return end();
  };
}
