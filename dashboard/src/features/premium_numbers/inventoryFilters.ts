import type { FilterFieldConfig, FilterValues, SortOption } from '../../components/FilterSortBar'
export const inventoryFilterFields:FilterFieldConfig[]=[{key:'q',label:'Search',type:'text'},{key:'status',label:'Status',type:'multiselect',options:[{value:'pending',label:'Pending'},{value:'active',label:'Active'},{value:'flagged',label:'Flagged'}]},{key:'category',label:'Category',type:'multiselect',options:[{value:'recruiter',label:'Recruiter'},{value:'employer',label:'Employer'}]},{key:'source_type',label:'Source',type:'select',options:[{value:'all',label:'All sources'},{value:'gmail',label:'Gmail'},{value:'nvoids',label:'Nvoids'}]},{key:'score',label:'Score',type:'range',min:0,max:100}]
export const inventorySortOptions:SortOption[]=[{value:'newest',label:'Newest first'},{value:'oldest',label:'Oldest first'},{value:'highest_score',label:'Highest score'},{value:'lowest_score',label:'Lowest score'}]
export const inventoryDefaultFilterValues:FilterValues={q:'',status:[],category:[],source_type:'all',score:{min:null,max:null},domain:'',favorite:'all'}
// Every parameter the Number Inventory endpoint accepts is built here, because
// useInventory fetches through this function directly rather than through the
// filterSortRegistry entry - domain, favorite and date used to be declared as
// fields but assembled only in the registry, so those three controls sent
// nothing and silently listed the unfiltered inventory.
export function inventoryFiltersToParams(v:FilterValues){const p:Record<string,string>={};const q=v.q as string;if(q?.trim())p.q=q.trim();for(const key of ['status','category'] as const){const values=v[key] as string[];if(values?.length)p[key]=values.join(',')}const source=v.source_type as string;if(source&&source!=='all')p.source_type=source;const score=v.score as {min:number|null;max:number|null};if(score?.min!=null)p.min_score=String(score.min);if(score?.max!=null)p.max_score=String(score.max);const domain=v.domain as string;if(domain?.trim())p.domain=domain.trim();const favorite=v.favorite as string;if(favorite&&favorite!=='all')p.favorite=favorite;return {...p,...dateParams(v)}}

// Mirrors the registry's own date handling so the same values reach the API
// whether the request is built here or from a pasted URL.
export function dateParams(v:FilterValues){const p:Record<string,string>={};const date=v.date as {preset:string;from:string|null;to:string|null}|undefined;if(!date||date.preset==='all')return p;p.date_filter=date.preset;if(date.preset==='custom'){if(date.from)p.date_from=date.from;if(date.to)p.date_to=date.to}return p}
