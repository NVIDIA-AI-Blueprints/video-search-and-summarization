import edgeHandler from '../../pages/api/chat';
import { createChatApiWrapper } from './apiWrapper';

// Pre-configured chat API handler ready to use
export const chatApiHandler = createChatApiWrapper(edgeHandler);
