export async function sendChatMessage(deps) {
  const {
    abortControllerRef,
    activeJobIdsRef,
    addMessage,
    callArchianAIArchitectAgent,
    cancelledRef,
    ensureActiveConversation,
    fileInputRef,
    isLoading,
    pendingReExtract,
    persistMessage,
    selectedFile,
    setAgentTurnSteps,
    setChatHistory,
    setIsLoading,
    setLoadingMessage,
    setPendingReExtract,
    setSelectedFile,
    setStreamingText,
    setUserMessage,
    startLoadingMessages,
    stopLoadingMessages,
    textareaRef,
    userMessage,
  } = deps;

    if (isLoading) return;
    if (!selectedFile && !userMessage.trim()) {
      return;
    }

    const conversationId = await ensureActiveConversation();
    if (!conversationId) {
      return;
    }

    // Clear pending re-extraction state if it exists (re-extraction now goes through streaming API)
    if (pendingReExtract) {
      setPendingReExtract(null);
    }

    const fileToSend = selectedFile;
    const userMetadata = fileToSend ? { fileName: fileToSend.name } : null;

    const newEntry = {
      id: `user-${Date.now()}`,
      type: 'user',
      text: userMessage,
      fileName: fileToSend ? fileToSend.name : null,
      metadata: userMetadata,
      timestamp: Date.now()
    };
    setChatHistory((prev) => [...prev, newEntry]);
    // Start dynamic loading messages based on context
    startLoadingMessages(userMessage, !!fileToSend);
    
    // Show loader immediately - before any async operations
    setIsLoading(true);
    // Clear any previous streaming text
    setStreamingText('');
    setAgentTurnSteps([]);

    // Clear input as soon as user sends (not when backend responds)
    setUserMessage('');
    setSelectedFile(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
    if (textareaRef.current) textareaRef.current.focus();

    // Keep loader visible during message persistence
    await persistMessage(conversationId, 'user', userMessage, userMetadata);

    // Create AbortController for this request (streaming + file upload)
    abortControllerRef.current = new AbortController();
    cancelledRef.current = false;
    activeJobIdsRef.current = new Set();
    const signal = abortControllerRef.current.signal;

    if (!fileToSend) {
      // Response is handled in the streaming function; loader stays visible until first chunk arrives
      try {
        const result = await callArchianAIArchitectAgent(userMessage, conversationId, signal);
        
        if (result === null) return; // User aborted
        
        // Check if streaming failed and we got an error message
        if (result && result.includes("❌ Streaming failed")) {
          addMessage(result, {
            id: `assistant-${Date.now()}`,
            type: 'system',
            timestamp: Date.now()
          });
          await persistMessage(conversationId, 'assistant', result);
          stopLoadingMessages();
          setIsLoading(false);
        } else if (result && result.trim()) {
          addMessage(result, {
            id: `assistant-${Date.now()}`,
            type: 'system',
            timestamp: Date.now()
          });
          await persistMessage(conversationId, 'assistant', result);
          setStreamingText('');
          stopLoadingMessages();
          setIsLoading(false);
        } else {
          setStreamingText('');
          stopLoadingMessages();
          setIsLoading(false);
        }
      } catch (error) {
        setIsLoading(false);
        const errorMessage = `❌ **Error:** ${error.message || 'Failed to get response. Please try again.'}`;
        addMessage(errorMessage, {
          id: `error-${Date.now()}`,
          type: 'system',
          timestamp: Date.now()
        });
        await persistMessage(conversationId, 'assistant', errorMessage);
      }
      
      stopLoadingMessages();
      setLoadingMessage('Preparing your data');
      return;
    }

    const messageText = userMessage;

    try {
      const result = await callArchianAIArchitectAgent(
        messageText,
        conversationId,
        signal,
        fileToSend,
      );
      if (result === null) return;
      if (result && result.includes('❌ Streaming failed')) {
        addMessage(result, {
          id: `assistant-${Date.now()}`,
          type: 'system',
          timestamp: Date.now(),
        });
        await persistMessage(conversationId, 'assistant', result);
        stopLoadingMessages();
        setIsLoading(false);
      } else if (result && result.trim()) {
        addMessage(result, {
          id: `assistant-${Date.now()}`,
          type: 'system',
          timestamp: Date.now(),
        });
        await persistMessage(conversationId, 'assistant', result);
        setStreamingText('');
        stopLoadingMessages();
        setIsLoading(false);
      } else {
        setStreamingText('');
        stopLoadingMessages();
        setIsLoading(false);
      }
    } catch (error) {
      setIsLoading(false);
      const errorMessage = `❌ **Error:** ${error.message || 'Failed to get response. Please try again.'}`;
      addMessage(errorMessage, {
        id: `error-${Date.now()}`,
        type: 'system',
        timestamp: Date.now(),
      });
      await persistMessage(conversationId, 'assistant', errorMessage);
    }
    stopLoadingMessages();
    setLoadingMessage('Preparing your data');
}
