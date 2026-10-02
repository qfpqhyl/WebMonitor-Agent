import * as React from "react";
import {cn} from "@/lib/utils";
export const Input=React.forwardRef<HTMLInputElement,React.ComponentProps<"input">>(({className,...props},ref)=><input ref={ref} className={cn("flex h-10 w-full min-w-0 rounded-lg border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",className)} {...props}/>);
Input.displayName="Input";
