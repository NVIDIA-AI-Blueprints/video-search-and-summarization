import { NemoAgentToolkitApp } from '@nemo-agent-toolkit/ui';

// Import server-side props from the library for SSR support  
export { getNemoAgentToolkitSSProps as getServerSideProps } from '@nemo-agent-toolkit/ui/server';

export default function HomePage() {
  return <NemoAgentToolkitApp />;
}