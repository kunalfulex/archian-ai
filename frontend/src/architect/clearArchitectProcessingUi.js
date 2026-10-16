import { createEmptyOperationPreview } from './reduceOperationPreview';
import { createEmptyPlanningActivity } from './reducePlanningActivity';
import { createEmptyRecoveryActivity } from './reduceRecoveryActivity';

/**
 * Hide architect stream loaders once migration (or another terminal tool action) has started.
 */
export function clearArchitectProcessingUi(handlers) {
  if (!handlers) return;
  handlers.clearExtractionMessageRotation?.();
  handlers.stopLoadingMessages?.();
  handlers.setIsLoading?.(false);
  handlers.clearOperationPreview?.();
  handlers.setAgentTurnSteps?.([]);
  handlers.setPlanningActivity?.(createEmptyPlanningActivity());
  handlers.setRecoveryActivity?.(createEmptyRecoveryActivity());
  if (handlers.setOperationPreview) {
    handlers.setOperationPreview(createEmptyOperationPreview());
  }
}
