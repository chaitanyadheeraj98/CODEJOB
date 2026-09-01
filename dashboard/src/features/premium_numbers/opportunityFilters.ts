import type { FilterFieldConfig,FilterValues,SortOption } from '../../components/FilterSortBar'
export const OPPORTUNITY_STATUS_OPTIONS=[
  {value:'New',label:'New'},
  {value:'Called',label:'Called'},
  {value:'Applied',label:'Applied'},
  {value:'Follow Up',label:'Follow Up'},
  {value:'Closed',label:'Closed'},
  {value:'Not Interested',label:'Not Interested'},
]
export const opportunityFilterFields:FilterFieldConfig[]=[
  {key:'job_title',label:'Job title',type:'combobox',bucket:'recruiter_opportunities'},
  {key:'end_client',label:'End client',type:'combobox',bucket:'recruiter_opportunities'},
  {key:'location',label:'Location',type:'combobox',bucket:'recruiter_opportunities'},
  {key:'employment_type',label:'Employment type',type:'combobox',bucket:'recruiter_opportunities'},
  {key:'work_mode',label:'Work mode',type:'combobox',bucket:'recruiter_opportunities'},
  {key:'job_confidence',label:'Job confidence',type:'combobox',bucket:'recruiter_opportunities'},
  {key:'extension_likely',label:'Extension likely',type:'combobox',bucket:'recruiter_opportunities'},
]
export const opportunitySortOptions:SortOption[]=[{value:'newest',label:'Newest first'},{value:'oldest',label:'Oldest first'}]
export const opportunityDefaultFilterValues:FilterValues={job_title:'',end_client:'',location:'',employment_type:'',work_mode:'',job_confidence:'',extension_likely:''}
export function opportunityFiltersToParams(values:FilterValues){const params:Record<string,string>={};for(const key of ['job_title','end_client','location','employment_type','work_mode','job_confidence','extension_likely']){const value=values[key] as string;if(value?.trim())params[key]=value.trim()}return params}
