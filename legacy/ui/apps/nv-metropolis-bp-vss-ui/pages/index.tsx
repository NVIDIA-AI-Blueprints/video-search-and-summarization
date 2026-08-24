// SPDX-License-Identifier: MIT
import { GetServerSideProps } from 'next';
import Head from 'next/head';

import Home from '../components/Home';
import { APPLICATION_TITLE } from '../constants/constants';

// Server-side props with data fetching
export const getServerSideProps: GetServerSideProps = async (context) => {
  try {
    // Import server-side functions dynamically
    const { getNemoAgentToolkitSSProps } = await import('@nemo-agent-toolkit/ui/server');
    const { fetchAlertsData, fetchSearchData, fetchDashboardData, fetchMapData, fetchVideoManagementData } = await import('@nv-metropolis-bp-vss-ui/all/server');
    
    // Get base props from NemoAgentToolkit (includes i18n translations)
    const nemoProps = await getNemoAgentToolkitSSProps(context);
    
    // Fetch data for our new components in parallel for better performance
    const [alertsData, searchData, dashboardData, mapData, videoManagementData] = await Promise.all([
      fetchAlertsData(),
      fetchSearchData(),
      fetchDashboardData(),
      fetchMapData(),
      fetchVideoManagementData(),
    ]);
    
    // Chain/Merge all props
    return {
      props: {
        ...nemoProps.props,        // Spread NemoAgentToolkit props (i18n, etc.)
        alertsData,                // Add Alerts data from package
        searchData,                // Add Search data from package
        dashboardData,             // Add Dashboard data from package
        mapData,                   // Add Map data from package
        videoManagementData,       // Add Video Management data from package
        serverRenderTime: new Date().toISOString(),
      },
    };
  } catch (error) {
    console.error('Error in getServerSideProps:', error);
    
    // Fallback: return minimal props if fetching fails
    return {
      props: {
        alertsData: null,
        dashboardData: null,
        mapData: null,
        searchData: null,
        videoManagementData: null,
        serverRenderTime: new Date().toISOString(),
      },
    };
  }
};

// Props interface matching what getServerSideProps returns
interface HomePageProps {
  alertsData?: any;
  dashboardData?: any;
  mapData?: any;
  searchData?: any;
  videoManagementData?: any;
  serverRenderTime?: string;
}

export default function HomePage(props: HomePageProps) {
  // Pass all SSR props to Home component
  return (
    <>
      <Head>
        <title>{APPLICATION_TITLE}</title>
      </Head>
      <Home {...props} />
    </>
  );
}