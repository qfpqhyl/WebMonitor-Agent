import type {NextConfig} from "next";
const config:NextConfig={output:"standalone",poweredByHeader:false,async rewrites(){return process.env.API_INTERNAL_ORIGIN?[{source:"/api/v1/:path*",destination:`${process.env.API_INTERNAL_ORIGIN}/api/v1/:path*`}]:[];}};
export default config;
