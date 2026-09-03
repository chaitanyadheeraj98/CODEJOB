import type { FilterFieldConfig, FilterValues, SortOption } from '../../components/FilterSortBar'
export const recycleBinFilterFields:FilterFieldConfig[]=[{key:'q',label:'Search',type:'text'},{key:'has_source_link',label:'Has evidence link',type:'boolean'}]
export const recycleBinSortOptions:SortOption[]=[{value:'newest',label:'Newest deleted first'},{value:'oldest',label:'Oldest deleted first'}]
export const recycleBinDefaultFilterValues:FilterValues={q:'',has_source_link:null}
export function recycleBinFiltersToParams(v:FilterValues){const p:Record<string,string>={};const q=v.q as string;if(q?.trim())p.q=q.trim();if(v.has_source_link!=null)p.has_source_link=String(v.has_source_link);return p}
