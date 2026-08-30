import type { FilterFieldConfig, FilterValues, SortOption } from '../../components/FilterSortBar'
export const recycleBinFilterFields:FilterFieldConfig[]=[{key:'q',label:'Search',type:'text'}]
export const recycleBinSortOptions:SortOption[]=[{value:'newest',label:'Newest deleted first'},{value:'oldest',label:'Oldest deleted first'}]
export const recycleBinDefaultFilterValues:FilterValues={q:''}
export function recycleBinFiltersToParams(v:FilterValues){const p:Record<string,string>={};const q=v.q as string;if(q?.trim())p.q=q.trim();return p}
