import type { FilterFieldConfig,FilterValues,SortOption } from '../../components/FilterSortBar'
export const opportunityFilterFields:FilterFieldConfig[]=[{key:'job_title',label:'Job title',type:'text'},{key:'end_client',label:'End client',type:'text'},{key:'location',label:'Location',type:'text'}]
export const opportunitySortOptions:SortOption[]=[{value:'newest',label:'Newest first'},{value:'oldest',label:'Oldest first'}]
export const opportunityDefaultFilterValues:FilterValues={job_title:'',end_client:'',location:''}
export function opportunityFiltersToParams(values:FilterValues){const params:Record<string,string>={};for(const key of ['job_title','end_client','location']){const value=values[key] as string;if(value?.trim())params[key]=value.trim()}return params}
