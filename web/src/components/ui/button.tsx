import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
export const buttonVariants=cva("inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4",{variants:{variant:{default:"bg-primary text-primary-foreground hover:opacity-90",outline:"border border-input bg-background hover:bg-muted",ghost:"hover:bg-muted",destructive:"bg-destructive text-white hover:opacity-90",secondary:"bg-secondary text-secondary-foreground hover:opacity-80",link:"text-primary underline-offset-4 hover:underline"},size:{default:"h-10 px-4 py-2",sm:"h-9 px-3",lg:"h-11 px-6",icon:"size-10"}},defaultVariants:{variant:"default",size:"default"}});
export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement>,VariantProps<typeof buttonVariants>{asChild?:boolean}
export const Button=React.forwardRef<HTMLButtonElement,ButtonProps>(({className,variant,size,asChild=false,...props},ref)=>{const Comp=asChild?Slot:"button";return <Comp ref={ref} className={cn(buttonVariants({variant,size,className}))} {...props}/>});
Button.displayName="Button";
