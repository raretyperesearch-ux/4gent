import dynamic from "next/dynamic";

const FourGent = dynamic(() => import("../4gent-final"), { ssr: false });

export default FourGent;
