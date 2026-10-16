/**
 * Line-buffered SSE reader: yields complete line chunks from a fetch body stream.
 */
export async function* consumeArchitectSseReader(reader, decoder, isCancelled) {
  let sseBuf = '';

  while (true) {
    if (isCancelled()) break;
    const { done, value } = await reader.read();
    if (isCancelled()) break;

    if (!done) {
      if (value?.length) sseBuf += decoder.decode(value, { stream: true });
    } else if (value?.length) {
      sseBuf += decoder.decode(value, { stream: false });
    }

    let chunk = '';
    if (done) {
      chunk = sseBuf;
      sseBuf = '';
    } else {
      const lastNl = sseBuf.lastIndexOf('\n');
      if (lastNl === -1) continue;
      chunk = sseBuf.slice(0, lastNl + 1);
      sseBuf = sseBuf.slice(lastNl + 1);
    }

    if (!chunk) {
      if (done) break;
      continue;
    }

    yield { chunk, done };
    if (done) break;
  }
}
